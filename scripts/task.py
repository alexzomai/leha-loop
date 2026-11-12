#!/usr/bin/env python3
"""Durable tasks for leha-loop. Standard library only; no daemon, auto-merge or Stop inference."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

from state import atomic_json

ACTIVE = {'starting', 'working', 'ready_for_review', 'reviewing', 'changes_requested', 'approved', 'merging', 'question', 'blocked', 'lost', 'failed'}
FINAL = {'merged', 'cancelled'}
REPORTS = {'progress', 'ready_for_review', 'question', 'blocked', 'failed', 'approved', 'changes_requested'}
SCRIPTS = Path(__file__).resolve().parent


def now(): return datetime.now(timezone.utc).isoformat()


class Error(Exception): pass


def run(argv, *, cwd=None, env=None, check=True):
    try:
        r = subprocess.run(list(map(str, argv)), cwd=cwd, env=env, text=True, encoding='utf-8', capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e: raise Error(str(e)) from e
    if check and r.returncode: raise Error(r.stderr.strip() or r.stdout.strip() or f'{argv[0]} failed')
    return r


def git(repo, *args): return run(['git', '-C', repo, *args]).stdout.strip()


def hc(*args):
    r = run(['hc', *args])
    try:
        data = json.loads(r.stdout)
        if isinstance(data, dict) and data.get('error'): raise Error('hc rejected the request')
        return data
    except ValueError as e: raise Error('hc returned invalid JSON') from e


def live_address(value):
    if not value: raise Error('an explicit live coordinator is required (--owner or HC_TERMINAL_ID)')
    t = hc('get', value)
    if t.get('state') != 'live' or not t.get('address'): raise Error('coordinator is not live')
    return t['address']


def scopes(values):
    result = []
    for value in values:
        value = value.rstrip('/')
        if value in ('', '.'): value = '.'
        p = PurePosixPath(value)
        if p.is_absolute() or '..' in p.parts or '\\' in value or any(ord(c) < 32 for c in value) or any(c in value for c in '*?['):
            raise Error('ownership must be literal repository-relative files/directories, not globs or parent paths')
        value = str(p)
        if value not in result: result.append(value)
    if not result: raise Error('declare at least one --owns file/directory (use . explicitly for the whole repository)')
    return result


def overlap(left, right):
    return any(a == '.' or b == '.' or a == b or a.startswith(b + '/') or b.startswith(a + '/') for a in left for b in right)


def owns(paths, name): return overlap(paths, [name])


def is_shell(t):
    fg = t.get('foreground') or {}
    argv = fg.get('argv') or t.get('argv') or []
    return bool(argv and Path(argv[0]).name in {'sh','bash','zsh','fish','dash','pwsh','powershell'} and
                (not t.get('status') or (fg.get('pid') is not None and fg.get('pid') == t.get('pid'))) and
                (not fg.get('pid') or fg.get('pid') == t.get('pid')))


class Registry:
    def __init__(self):
        self.repo = str(Path(os.environ['REPO']).resolve())
        self.project = os.environ['PROJ']
        self.home = Path(os.environ.get('FB_TASK_HOME', Path.home() / '.local/share/leha-loop' / self.project))
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.home / 'tasks.json'
        self.runtime = Path(os.environ['STATE_FILE'])
        self.base = os.environ.get('BASE', 'main')
        self.maximum = int(os.environ.get('MAX_PARALLEL_WORKERS', '3'))
        if self.maximum < 1: raise Error('MAX_PARALLEL_WORKERS must be positive')

    @contextmanager
    def lock(self, name='registry'):
        with open(self.home / (name + '.lock'), 'a') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield

    def read(self):
        if not self.path.exists(): return {'version': 1, 'repo': self.repo, 'project': self.project, 'sequence': 0, 'tasks': {}, 'outbox': {}}
        try: data = json.loads(self.path.read_text(encoding='utf-8'))
        except (ValueError, OSError) as e: raise Error('task registry unreadable; refusing to reset or overwrite it') from e
        if data.get('version') != 1 or data.get('repo') != self.repo or not isinstance(data.get('tasks'), dict):
            raise Error('registry version/repository mismatch; use a separate FB_PROJ/FB_TASK_HOME')
        return data

    def save(self, data): atomic_json(self.path, data)

    def runtime_data(self):
        if not self.runtime.exists(): return {}
        try:
            data = json.loads(self.runtime.read_text(encoding='utf-8'))
            if not isinstance(data, dict): raise ValueError('not an object')
            return data
        except (OSError, ValueError) as e: raise Error('runtime state unreadable; cannot safely allocate a worker') from e

    def find(self, data, key):
        if key in data['tasks']: return data['tasks'][key]
        matches = [t for t in data['tasks'].values() if t['branch'] == key]
        if matches: return max(matches, key=lambda t: t['number'])
        raise Error(f'unknown task: {key}')

    def owner(self, value=None): return live_address(value or os.environ.get('FB_PARENT') or os.environ.get('HC_TERMINAL_ID'))

    def actor(self, task, worker=False):
        address = live_address(os.environ.get('HC_TERMINAL_ID') or os.environ.get('FB_PARENT'))
        expected = task.get('terminal') if worker else task['owner']
        if address != expected: raise Error('command must be run by the recorded ' + ('worker' if worker else 'coordinator'))
        return address

    def event(self, task, kind, **details):
        task['updated_at'] = now()
        task.setdefault('history', []).append({'at': task['updated_at'], 'kind': kind, **details})

    def text_file(self, path):
        b = Path(path).read_bytes()
        if not b.strip() or len(b) > 1024 * 1024: raise Error('brief/report must be nonempty and at most 1 MiB')
        return b.decode('utf-8')

    def write_text(self, path, body):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(prefix='.', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(body); f.flush(); os.fsync(f.fileno())
            os.replace(tmp, path)
            directory=os.open(path.parent,os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)

    def add(self, data, branch, name, brief, owned, owner, dependencies=()):
        git(self.repo, 'check-ref-format', '--branch', branch)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', name): raise Error('terminal name must be 1–32 letters, digits, hyphens or underscores')
        if any(t['state'] not in FINAL and (t['branch'] == branch or t['worker'] == name) for t in data['tasks'].values()):
            raise Error('an unfinished task already uses that branch or terminal name')
        dependencies = list(dict.fromkeys(dependencies))
        for key in dependencies:
            if key not in data['tasks']: raise Error(f'dependency must name an existing task ID: {key}')
        data['sequence'] += 1
        key = f'T{data["sequence"]:04d}'
        owned = scopes(owned)
        if Path(self.repo, 'openspec').is_dir(): owned = scopes([*owned, f'openspec/changes/{branch}'])
        text = self.text_file(brief)
        copy = self.home / 'briefs' / f'{key}.md'
        self.write_text(copy, text)
        task = {'id': key, 'number': data['sequence'], 'branch': branch, 'worker': name, 'brief': str(copy),
                'owns': owned, 'owner': owner, 'depends_on': dependencies, 'state': 'todo', 'repo': self.repo,
                'base': self.base, 'created_at': now(), 'updated_at': now(), 'history': [], 'attempts': 0}
        self.event(task, 'created')
        data['tasks'][key] = task
        return task

    def reason(self, data, task):
        waiting = [key for key in task['depends_on'] if data['tasks'][key]['state'] != 'merged']
        if waiting: return 'dependencies not accepted/merged: ' + ', '.join(waiting)
        active = [t for t in data['tasks'].values() if t['id'] != task['id'] and t['state'] in ACTIVE]
        legacy = [b for b, value in self.runtime_data().items() if value.get('phase') not in {'merged', 'cleaned', 'cancelled'} and not any(
            t['branch']==b and (value.get('task_id')==t['id'] or (value.get('term') and value.get('term')==t.get('terminal'))) for t in data['tasks'].values())]
        if legacy: return 'legacy workers have unknown ownership; adopt them first: ' + ', '.join(legacy)
        if len(active) >= self.maximum: return f'parallel limit {self.maximum}'
        conflicts = [t['id'] for t in active if overlap(task['owns'], t['owns'])]
        if conflicts: return 'file ownership held by: ' + ', '.join(conflicts)
        return None

    def claim(self, data, task, resume=False):
        if task['base'] != self.base: raise Error('BASE changed since task creation; inspect the task instead of silently changing its target')
        reason = self.reason(data, task)
        if reason: raise Error(reason)
        task.update(state='starting', claim=uuid.uuid4().hex, launch_attempted=False, resume=resume)
        task['attempts'] += 1
        self.event(task, 'claimed', resume=resume)
        return task['claim']

    def reserve(self, branch, name, brief, owner):
        with self.lock():
            data = self.read()
            key = os.environ.get('LEHA_TASK_ID')
            if os.environ.get('LEHA_TASK_RESUME') == '1' and not key: raise Error('resume requires an explicit registry claim')
            if key:
                task = self.find(data, key)
                if (task['state'] != 'starting' or task.get('claim') != os.environ.get('LEHA_TASK_CLAIM') or
                    task.get('launch_attempted') or (task['branch'], task['worker'], task['owner']) != (branch, name, owner) or
                    Path(brief).resolve() != Path(task['brief']).resolve()):
                    raise Error('invalid or already consumed task launch claim')
            else:
                task = self.add(data, branch, name, brief, ['.'], owner)
                try: self.claim(data, task)
                except Error:
                    task['state']='queued'; self.event(task,'queued'); self.save(data)
                    raise Error('direct launch queued as '+task['id']+'; inspect task.sh list, then use task.sh schedule')
            self.save(data)
            return task['id']

    def phase(self, branch, phase, pairs):
        with self.lock():
            data = self.read()
            try: task = self.find(data, branch)
            except Error: return  # Existing unregistered branches keep their old workflow.
            if task['state'] in FINAL: return
            values = dict(zip(pairs[::2], pairs[1::2]))
            if phase == 'preparing':
                if task['state'] != 'starting': raise Error('task is not reserved for setup')
            elif phase == 'launching':
                if task['state'] != 'starting' or task.get('launch_attempted'): raise Error('launch already attempted')
                task['launch_attempted'] = True
            elif phase == 'working':
                if task['state'] != 'starting': raise Error('task is not reserved for launch')
                task['state'] = 'working'
            elif phase in {'failed', 'lost'}:
                if task['state'] not in {'starting','working','lost'}: return
                task['state'] = phase
            elif phase == 'reviewing':
                if task['state'] not in {'ready_for_review','changes_requested','reviewing'}: raise Error('report ready_for_review before starting review')
                task['state'] = 'reviewing'
            elif phase == 'merging':
                if task['state'] not in {'approved','merging'}: raise Error('report approved after verdict before merging')
                if task.get('approved_sha') != self.check_approval(task): raise Error('report approved for the current SHA before merging')
                task['state'] = 'merging'
            task.update(values)
            self.event(task, phase)
            self.save(data)

    def check_checkout(self, task):
        wt = task.get('worktree')
        if not wt or not Path(wt).is_dir(): raise Error('recorded worktree is missing; no checkout will be recreated automatically')
        if git(wt, 'rev-parse', '--show-toplevel') != str(Path(wt).resolve()) or git(wt, 'branch', '--show-current') != task['branch']:
            raise Error('recorded worktree no longer holds the task branch')
        return wt

    def check_ready(self, task):
        if task['base'] != self.base: raise Error('BASE changed since task creation; review the task configuration')
        wt = self.check_checkout(task)
        if git(wt, 'status', '--porcelain'): raise Error('commit work before reporting ready/approved')
        head = git(wt, 'rev-parse', 'HEAD')
        changed = run(['git','-C',wt,'diff','--name-only','-z',f'{task["base"]}...HEAD']).stdout.split('\0')
        outside = [p for p in changed if p and not owns(task['owns'], p)]
        if outside: raise Error('changes outside declared ownership: ' + ', '.join(outside))
        return head

    def check_approval(self, task):
        head = self.check_ready(task)
        run(['bash', SCRIPTS / 'verdict.sh', task['branch']], cwd=self.repo)
        return head

    def deliver(self, key, retry_uncertain=False):
        with self.lock('delivery'):
            with self.lock():
                data = self.read(); item = data['outbox'][key]
                if item['delivery'] == 'delivered': return 'delivered'
                if item['delivery'] == 'sending' and not retry_uncertain: return 'uncertain — inspect before retrying'
                owner = item['owner']
                try:
                    terminal = hc('get', owner)
                    if terminal.get('state') != 'live' or is_shell(terminal): raise Error('owner is gone or is a shell')
                    status = terminal.get('status') or {}
                    if not (isinstance(status, dict) and status.get('app')) and not terminal.get('program', {}).get('agent'):
                        raise Error('owner program cannot be verified; not sending text to an unknown terminal')
                except Error as e:
                    item.update(delivery='pending', error=str(e)); self.save(data); return 'pending'
                item['delivery'] = 'sending'; self.save(data)
            try:
                # Never --force: a real permission prompt must not be answered by a task report.
                run(['hc', 'send', owner, item['message']])
            except Error as e:
                with self.lock():
                    data = self.read(); data['outbox'][key].update(delivery='sending', error=str(e)); self.save(data)
                return 'uncertain — inspect before retrying'
            with self.lock():
                data = self.read(); data['outbox'][key].update(delivery='delivered', delivered_at=now()); self.save(data)
            return 'delivered'

    def report(self, key, state, file):
        if state not in REPORTS: raise Error('unsupported worker report state')
        body = self.text_file(file)
        with self.lock():
            data = self.read(); task = self.find(data, key); self.actor(task, worker=True)
            if task['state'] in FINAL: raise Error('task is already final')
            head = self.check_ready(task) if state in {'ready_for_review','approved'} else ''
            if state == 'approved': head = self.check_approval(task)
            digest = hashlib.sha256((task['id'] + '\0' + state + '\0' + head + '\0' + body).encode()).hexdigest()
            if digest not in data['outbox']:
                report = self.home / 'reports' / task['id'] / f'{digest}.md'
                self.write_text(report, body)
                task['state'] = {'progress':'working'}.get(state, state)
                task['report'] = str(report)
                if head: task['reported_sha'] = head
                if state == 'approved': task['approved_sha'] = head
                self.event(task, 'reported', report_state=state, report=str(report), sha=head)
                data['outbox'][digest] = {'task':task['id'], 'owner':task['owner'], 'delivery':'pending',
                    'message':f'LEHA REPORT {task["id"]} {state} {task["branch"]}{"@"+head if head else ""}\nReport: {report}\n{body}'}
                self.save(data)
        # Only the actual independent worker may update its own attention label.
        attention = shutil.which('herdr-attention')
        if attention:
            phase = {'progress':'working','ready_for_review':'review','approved':'review','changes_requested':'working','failed':'blocked'}.get(state,state)
            try:
                t = hc('get', task['terminal'])
                r = json.loads(t.get('labels',{}).get('herdr.attention','null')) or {}
                if r.get('role') != 'worker' or r.get('owner') != task['owner']:
                    run([attention,'start','--role','worker','--owner',task['owner'],'--title',task['branch']])
                run([attention,phase,'--title',f'{task["id"]}: {state}'])
            except (Error,ValueError): pass  # The durable report still stands; never fabricate user completion.
        return {'task':task['id'], 'state':task['state'], 'report':task['report'], 'delivery':self.deliver(digest)}

    def snapshot(self):
        result = hc('ls','--all','--json')
        terminals = result if isinstance(result,list) else result['terminals']
        # ls can omit foreground/argv. A kept shell must not be mistaken for the old worker.
        for i,t in enumerate(terminals):
            if t.get('state')=='live':
                try: terminals[i]=hc('get',t['address'])
                except Error: pass  # Unknown live program stays a conservative no-restart case.
        return terminals

    def recover(self, apply=False):
        if apply:
            with self.lock('launch'): return self._recover(True)
        return self._recover(False)

    def _recover(self, apply):
        # Query first; a disconnected core must not turn every task into "lost".
        terminals = self.snapshot()
        with self.lock():
            data = self.read(); result = []
            for task in data['tasks'].values():
                if task['state'] not in ACTIVE: continue
                matches = [t for t in terminals if t.get('state') == 'live' and
                    (t.get('address') == task.get('terminal') or (t.get('labels',{}).get('leha.task') == task['id'] and t.get('labels',{}).get('leha.project') == self.project))]
                agents = [t for t in matches if not is_shell(t)]
                if len(agents) > 1: action = 'ambiguous — inspect; do not restart'
                elif agents:
                    terminal = agents[0]
                    status = terminal.get('status') or {}
                    verified = (isinstance(status,dict) and status.get('app')) or terminal.get('program',{}).get('agent')
                    action = 'live — leave running' if verified else 'live program unknown — inspect; do not restart'
                    if apply and verified and task['state'] in {'lost','starting'}:
                        task.update(terminal=terminal['address'],state='working'); self.event(task,'reattached')
                else:
                    action = 'lost — explicit resume required; keep existing checkout'
                    if apply: task['state']='lost'; self.event(task,'lost')
                result.append({'id':task['id'],'branch':task['branch'],'action':action,'worktree':task.get('worktree')})
            if apply: self.save(data)
            return result

    def launch(self, task, claim, launch_locked=False):
        env = {**os.environ, 'LEHA_TASK_ID':task['id'], 'LEHA_TASK_CLAIM':claim, 'FB_PARENT':task['owner']}
        if launch_locked: env['LEHA_LAUNCH_LOCKED']=self.repo
        if task.get('resume'):
            env.update(LEHA_TASK_RESUME='1', LEHA_TASK_WORKTREE=task['worktree'], LEHA_TASK_PORT=str(task['port']))
        else:
            for k in ('LEHA_TASK_RESUME','LEHA_TASK_WORKTREE','LEHA_TASK_PORT'): env.pop(k,None)
        # Launch may include project setup; no registry lock is held during slow work.
        try:
            r = subprocess.run(['bash',str(SCRIPTS/'mkworker.sh'),task['branch'],task['worker'],task['brief']],cwd=self.repo,env=env,text=True,encoding='utf-8',capture_output=True)
        except OSError as e: r = subprocess.CompletedProcess([],1,'',str(e))
        if r.returncode:
            with self.lock():
                data=self.read(); current=self.find(data,task['id'])
                if current['state']=='starting':
                    current['state']='lost' if current.get('launch_attempted') else 'failed'
                    self.event(current,'launch_error'); self.save(data)
            raise Error(r.stderr.strip() or 'worker launch failed; inspect task recovery before retrying')
        return {'id':task['id'],'launch':r.stdout.strip()}

    def schedule(self, selected=None):
        actor=live_address(os.environ.get('HC_TERMINAL_ID') or os.environ.get('FB_PARENT'))
        results=[]
        with self.lock('dispatch'):
            with self.lock():
                data=self.read()
                if selected:
                    task=self.find(data,selected); self.actor(task)
                    if task['state'] not in {'todo','queued'}: raise Error('only todo/queued tasks may start')
                    task['state']='queued'; self.event(task,'queued'); self.save(data)
            # Stable order; one dispatcher; no retry of ambiguous starts.
            for key in list(self.read()['tasks']):
                with self.lock():
                    data=self.read(); task=data['tasks'][key]
                    if task['state']!='queued' or task['owner']!=actor: continue
                    reason=self.reason(data,task)
                    if reason:
                        results.append({'id':key,'queued':reason}); continue
                    claim=self.claim(data,task); self.save(data)
                results.append(self.launch(task,claim))
        return results

    def resume(self, key):
        with self.lock('dispatch'), self.lock('launch'):
            terminals=self.snapshot()
            with self.lock():
                data=self.read(); task=self.find(data,key)
                # Never respawn if a live agent/unknown program exists, even if the record says lost.
                matches=[t for t in terminals if t.get('state')=='live' and
                    (t.get('address')==task.get('terminal') or (t.get('labels',{}).get('leha.task')==task['id'] and t.get('labels',{}).get('leha.project')==self.project))]
                if any(not is_shell(t) for t in matches): raise Error('worker is live or ambiguous; no duplicate will be started')
                if task['state'] not in {'lost','failed','starting'}: raise Error('recover/apply first; only lost/failed launches may resume')
                self.check_checkout(task)
                if not task.get('port') or not 1 <= int(task['port']) <= 65535: raise Error('recorded worker port is missing/invalid')
                task['owner']=self.owner()
                task['worker']=(task['worker'][:22]+'-r'+str(task['attempts']+1))
                task.pop('approved_sha',None)
                claim=self.claim(data,task,resume=True); self.save(data)
            return self.launch(task,claim,launch_locked=True)

    def accept(self,key):
        with self.lock():
            data=self.read(); task=self.find(data,key); self.actor(task)
            if task['state']=='merged': return {'id':task['id'],'state':'merged'}
            if task['state'] not in {'approved','merging'}: raise Error('task must pass its review gate before acceptance')
            wt=self.check_checkout(task)
            head=git(wt,'rev-parse','HEAD')
            if task.get('approved_sha')!=head or git(wt,'status','--porcelain'): raise Error('approved SHA changed or checkout is dirty')
            review=Path(os.environ['REVIEWS']) / ('review-'+task['branch']+'.md')
            lines=[line.strip().strip('`* ') for line in review.read_text(encoding='utf-8').splitlines() if line.strip()]
            match=re.match(re.escape(task['branch'])+r'@([0-9a-fA-F]+): APPROVED(?:\s|$)',lines[-1] if lines else '')
            if not match or git(self.repo,'rev-parse',match[1]+'^{commit}')!=head: raise Error('current reviewer report no longer approves this SHA')
            # Acceptance verifies actual Git ancestry, never the worker merely saying "done".
            git(self.repo,'merge-base','--is-ancestor',head,task['base'])
            task['state']='merged'; self.event(task,'accepted',sha=head); self.save(data)
            return {'id':task['id'],'state':'merged'}

    def cancel(self, key):
        with self.lock('dispatch'), self.lock('launch'):
            terminals=self.snapshot()
            with self.lock():
                data=self.read(); task=self.find(data,key); self.actor(task)
                if task['state'] in FINAL or task['state']=='merging': raise Error('final/merging tasks cannot be cancelled here')
                related={task['terminal']} if task.get('terminal') else set()
                for _ in terminals:
                    related.update(t.get('address') for t in terminals if t.get('parent') in related)
                if any(t.get('state')=='live' and not is_shell(t) and
                    (t.get('address') in related or (t.get('labels',{}).get('leha.task')==task['id'] and t.get('labels',{}).get('leha.project')==self.project)) for t in terminals):
                    raise Error('task or reviewer is still live; cancellation never kills or abandons it automatically')
                task['state']='cancelled'; self.event(task,'cancelled'); self.save(data)
                return {'id':task['id'],'state':'cancelled'}

    def change_scope(self, key, owned):
        owned=scopes(owned)
        with self.lock():
            data=self.read(); task=self.find(data,key); self.actor(task)
            if task['state'] in FINAL or task['state']=='merging': raise Error('cannot change scope of a final/merging task')
            if Path(self.repo,'openspec').is_dir(): owned=scopes([*owned,'openspec/changes/'+task['branch']])
            if task['state'] in ACTIVE and any(t['id']!=task['id'] and t['state'] in ACTIVE and overlap(owned,t['owns']) for t in data['tasks'].values()):
                raise Error('new ownership overlaps another active task; serialize or finish that task first')
            task['owns']=owned; self.event(task,'scope_changed'); self.save(data)
            return task

    def adopt(self, branch, owned, brief=None):
        runtime=self.runtime_data().get(branch)
        if not runtime or not runtime.get('term'): raise Error('no legacy worker record for that branch')
        terminal=hc('get',runtime['term'])
        status=terminal.get('status') or {}
        if terminal.get('state')!='live' or is_shell(terminal) or not ((isinstance(status,dict) and status.get('app')) or terminal.get('program',{}).get('agent')):
            raise Error('legacy worker is not a verified live agent')
        owner=terminal.get('parent') or runtime.get('parent')
        if not owner: raise Error('legacy worker has no recorded owner; do not guess from focus')
        if '/' in terminal.get('address','') and '/' not in owner: owner=terminal['address'].split('/')[0]+'/'+owner
        source=brief or runtime.get('task')
        if not source: raise Error('provide the original --brief')
        with self.lock():
            data=self.read(); task=self.add(data,branch,runtime.get('worker') or terminal.get('name'),source,owned,owner)
            task.update(state='working',terminal=terminal['address'],worktree=runtime['wt'],port=runtime.get('port'))
            self.check_checkout(task); self.event(task,'adopted_metadata_only'); self.save(data)
            return task


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('add'); a.add_argument('--branch',required=True); a.add_argument('--name',required=True); a.add_argument('--brief',required=True); a.add_argument('--owns',action='append',required=True); a.add_argument('--owner'); a.add_argument('--depends-on',action='append',default=[])
    sub.add_parser('list')
    sub.add_parser('_ports')
    for cmd in ['show','start','resume','accept','cancel']:
        a=sub.add_parser(cmd); a.add_argument('task')
    sub.add_parser('schedule')
    a=sub.add_parser('report'); a.add_argument('task'); a.add_argument('--state',choices=sorted(REPORTS),required=True); a.add_argument('--file',required=True)
    a=sub.add_parser('recover'); a.add_argument('--apply',action='store_true')
    a=sub.add_parser('deliver'); a.add_argument('--retry-uncertain',action='store_true')
    a=sub.add_parser('scope'); a.add_argument('task'); a.add_argument('--owns',action='append',required=True)
    a=sub.add_parser('adopt'); a.add_argument('branch'); a.add_argument('--owns',action='append',required=True); a.add_argument('--brief')
    a=sub.add_parser('_reserve'); a.add_argument('branch'); a.add_argument('name'); a.add_argument('brief'); a.add_argument('owner')
    a=sub.add_parser('_phase'); a.add_argument('branch'); a.add_argument('phase'); a.add_argument('pairs',nargs='*')
    a=sub.add_parser('_locked-launch'); a.add_argument('args',nargs=argparse.REMAINDER)
    a=sub.add_parser('_require-accepted'); a.add_argument('branch')
    return p


def main():
    args=parser().parse_args(); r=Registry(); cmd=args.command
    if cmd=='_locked-launch':
        with r.lock('launch'):
            env={**os.environ,'LEHA_LAUNCH_LOCKED':r.repo}
            return subprocess.call(['bash',str(SCRIPTS/'mkworker.sh'),*args.args],cwd=r.repo,env=env)
    if cmd=='_ports':
        print(' '.join(str(t['port']) for t in r.read()['tasks'].values() if t['state'] in ACTIVE and t.get('port'))); return 0
    if cmd=='_reserve': print(r.reserve(args.branch,args.name,args.brief,args.owner)); return 0
    if cmd=='_phase':
        if len(args.pairs)%2: raise Error('expected key/value pairs')
        r.phase(args.branch,args.phase,args.pairs); return 0
    if cmd=='add':
        owner=r.owner(args.owner)
        with r.lock():
            data=r.read(); result=r.add(data,args.branch,args.name,args.brief,args.owns,owner,args.depends_on); r.save(data)
    elif cmd=='list': result=[{k:t.get(k) for k in ['id','branch','state','owner','terminal','depends_on','owns','worktree','report']} for t in r.read()['tasks'].values()]
    elif cmd=='show': result=r.find(r.read(),args.task)
    elif cmd=='start': result=r.schedule(args.task)
    elif cmd=='schedule': result=r.schedule()
    elif cmd=='report': result=r.report(args.task,args.state,args.file)
    elif cmd=='recover': result=r.recover(args.apply)
    elif cmd=='resume': result=r.resume(args.task)
    elif cmd=='accept':
        result=r.accept(args.task)
        result['queue']=r.schedule()
    elif cmd=='scope': result=r.change_scope(args.task,args.owns)
    elif cmd=='adopt': result=r.adopt(args.branch,args.owns,args.brief)
    elif cmd=='deliver': result={k:r.deliver(k,args.retry_uncertain) for k,item in r.read()['outbox'].items() if item['delivery']!='delivered'}
    elif cmd=='cancel': result=r.cancel(args.task)
    elif cmd=='_require-accepted':
        data=r.read()
        try: task=r.find(data,args.branch)
        except Error: return 0
        if task['state']!='merged': raise Error('accept the reviewed/merged task before cleanup: task.sh accept '+task['id'])
        return 0
    else: raise Error('unknown command')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    try: sys.exit(main())
    except (Error,ValueError,OSError,KeyError) as e:
        print('leha-task: '+str(e),file=sys.stderr); sys.exit(1)
