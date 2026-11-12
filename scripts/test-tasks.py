#!/usr/bin/env python3
"""Real temporary Git worktrees and fake Core. Never launches agents or touches user projects."""
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE=Path(__file__).resolve().parent
HC=r'''#!/usr/bin/env python3
import fcntl,json,os,sys
from pathlib import Path
root=Path(os.environ['FIXTURE']); args=sys.argv[1:]
with open(root/'hc.lock','a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 db=json.loads((root/'hc.json').read_text())
 with open(root/'calls.jsonl','a') as f:f.write(json.dumps(args)+'\n')
 if args[0]=='get':
  t=next((v for k,v in db.items() if args[1] in [k,v.get('name')]),None)
  if t is None:sys.exit(3)
  print(json.dumps(t))
 elif args[0]=='ls':
  if os.environ.get('HC_LS_FAIL'):sys.exit(3)
  print(json.dumps({'terminals':list(db.values())}))
 elif args[0]=='run':
  address='t'+str(len(db)+1); labels={}
  for i,a in enumerate(args):
   if a=='--label':k,v=args[i+1].split('=',1);labels[k]=v
  db[address]={'address':address,'name':args[args.index('--name')+1],'parent':args[args.index('--parent')+1],
   'cwd':args[args.index('--cwd')+1],'state':'live','labels':labels,'status':{'state':'working','app':'claude-code'}}
  (root/'hc.json').write_text(json.dumps(db))
  print('{}' if os.environ.get('HC_EMPTY_RUN') else json.dumps({'address':address}))
 elif args[0]=='send':
  if os.environ.get('HC_SEND_FAIL'):sys.exit(3)
  print('sent')
 elif args[0]=='label':
  for value in args[2:]:k,v=value.split('=',1);db[args[1]]['labels'][k]=v
  (root/'hc.json').write_text(json.dumps(db));print('{}')
 else:sys.exit(4)
'''
LAYOUT="import sys\nprint('fixture-space fixture-tab' if sys.argv[1]=='add-space' else 'ok')\n"


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='leha-tasks-')
        self.root=Path(self.tmp.name); self.repo=self.root/'repo'; self.repo.mkdir()
        self.home=self.root/'home'; self.home.mkdir()
        self.skill=self.root/'skill'; shutil.copytree(SOURCE,self.skill/'scripts',ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(SOURCE.parent/'roles',self.skill/'roles')
        with open(self.skill/'scripts/common.sh','a') as f:f.write('\nfb_accept_trust() { :; }\nfb_port_busy() { return 1; }\n')
        (self.skill/'scripts/layout.py').write_text(LAYOUT)
        (self.root/'bin').mkdir(); (self.root/'bin/hc').write_text(HC); (self.root/'bin/hc').chmod(0o755)
        conf=self.home/'.config/leha-loop';conf.mkdir(parents=True)
        (conf/'fixture.env').write_text('WORKER_CMD=(fixture-worker --model fixture)\nMERGER_RESET=""\nWORKER_PORTS=4100-4120\nPREFIX=fixture-\n')
        self.env={k:v for k,v in os.environ.items() if not k.startswith(('FB_','LEHA_','HC_'))}
        self.env.update(HOME=str(self.home),FIXTURE=str(self.root),FB_REPO=str(self.repo),FB_PROJ='fixture',FB_TASK_HOME=str(self.root/'durable'),
            HC_TERMINAL_ID='boss',MAX_PARALLEL_WORKERS='2',PATH=str(self.root/'bin')+':/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin',
            GIT_CONFIG_GLOBAL=os.devnull,GIT_CONFIG_SYSTEM=os.devnull,GIT_AUTHOR_NAME='Fixture',GIT_AUTHOR_EMAIL='fixture@example.invalid',GIT_COMMITTER_NAME='Fixture',GIT_COMMITTER_EMAIL='fixture@example.invalid')
        self.db({'boss':{'address':'boss','name':'boss','state':'live','status':{'state':'idle','app':'pi'},'labels':{'herdr.space':'boss-space'}}})
        self.git('init','-q','-b','main')
        for name in ['a.txt','b.txt','c.txt']:(self.repo/name).write_text('base\n')
        self.git('add','.');self.git('-c','commit.gpgsign=false','commit','-qm','base')
        self.brief=self.root/'brief.md';self.brief.write_text('Fixture bounded task; no secrets.')
        self.report=self.root/'report.md';self.report.write_text('Fixture evidence and checks.')
    def tearDown(self):self.tmp.cleanup()
    def db(self,value=None):
        path=self.root/'hc.json'
        if value is not None:path.write_text(json.dumps(value))
        return json.loads(path.read_text())
    def git(self,*args,where=None):
        return subprocess.check_output(['git','-C',str(where or self.repo),*args],env=self.env,text=True,stderr=subprocess.STDOUT).strip()
    def call(self,*args,ok=True,env=None,script='task.sh'):
        r=subprocess.run(['bash',str(self.skill/'scripts'/script),*map(str,args)],cwd=self.repo,env={**self.env,**(env or {})},text=True,capture_output=True,timeout=30)
        if ok:self.assertEqual(r.returncode,0,r.stderr)
        else:self.assertNotEqual(r.returncode,0,r.stdout)
        return r
    def cli(self,*args,**kw):return json.loads(self.call(*args,**kw).stdout)
    def add(self,branch='feature',owned='a.txt',deps=(),owner=None):
        args=['add','--branch',branch,'--name','w-'+branch,'--brief',self.brief,'--owns',owned]
        for d in deps:args.extend(['--depends-on',d])
        if owner:args.extend(['--owner',owner])
        return self.cli(*args)['id']
    def task(self,key):return self.cli('show',key)
    def launch(self,key):self.cli('start',key);return self.task(key)
    def calls(self,kind):
        p=self.root/'calls.jsonl'
        return [x for x in map(json.loads,p.read_text().splitlines()) if x[0]==kind] if p.exists() else []
    def change(self,t,file='a.txt',body='feature\n'):
        (Path(t['worktree'])/file).write_text(body);self.git('add',file,where=t['worktree']);self.git('-c','commit.gpgsign=false','commit','-qm','change',where=t['worktree'])
        return self.git('rev-parse','HEAD',where=t['worktree'])
    def report_as(self,t,state,**kw):return self.cli('report',t['id'],'--state',state,'--file',self.report,env={'HC_TERMINAL_ID':t['terminal'],**kw})
    def approve(self,t):
        self.report_as(t,'ready_for_review')
        head=self.git('rev-parse','HEAD',where=t['worktree'])
        review=self.home/'.cache/leha-loop/fixture/reviews'/('review-'+t['branch']+'.md')
        review.write_text(t['branch']+'@'+head+': APPROVED — fixture\n')
        self.report_as(t,'approved');return head
    def finish_terminal(self,t):
        db=self.db();db[t['terminal']]['state']='finished';self.db(db)

    def test_brief_is_durable_and_no_agent_starts_on_add(self):
        key=self.add();t=self.task(key);self.brief.unlink()
        self.assertIn('Fixture',Path(t['brief']).read_text());self.assertEqual(t['state'],'todo');self.assertFalse(self.calls('run'))
    def test_native_launch_and_metadata(self):
        t=self.launch(self.add());call=self.calls('run')[0]
        self.assertEqual(call[call.index('--parent')+1],'boss');self.assertIn('--keep',call);self.assertIn('--outlive-parent',call)
        self.assertIn('leha.task='+t['id'],call);self.assertIn('leha.project=fixture',call);self.assertEqual(t['state'],'working')
        self.assertIn('FB_PROJ=fixture',call);self.assertIn('FB_REPO='+str(self.repo),call);self.assertIn('FB_TASK_HOME='+str(self.root/'durable'),call)
        runtime=json.loads((self.home/'.cache/leha-loop/fixture/state.json').read_text())
        self.assertEqual(runtime['feature']['task_id'],t['id']);self.assertTrue(any('herdr.space=fixture-space' in c for c in self.calls('label')))
    def test_direct_launcher_registers_conservative_ownership(self):
        self.call('direct','direct-worker',self.brief,script='mkworker.sh')
        ts=self.cli('list');self.assertEqual(len(ts),1);self.assertEqual(ts[0]['owns'],['.']);self.assertEqual(ts[0]['state'],'working')
    def test_capacity_queues_without_starting_extra_worker(self):
        self.env['MAX_PARALLEL_WORKERS']='1';a=self.add('one');b=self.add('two','b.txt')
        self.launch(a);self.cli('start',b);self.assertEqual(self.task(b)['state'],'queued');self.assertEqual(len(self.calls('run')),1)
    def test_overlapping_scopes_are_serialized(self):
        a=self.add('one','src');b=self.add('two','src/api');self.launch(a);self.cli('start',b)
        self.assertEqual(self.task(b)['state'],'queued');self.assertEqual(len(self.calls('run')),1)
    def test_disjoint_scopes_run_concurrently(self):
        self.launch(self.add('one'));self.launch(self.add('two','b.txt'));self.assertEqual(len(self.calls('run')),2)
    def test_dependencies_wait_for_reviewed_actual_merge_and_acceptance(self):
        a=self.add('one');b=self.add('two','b.txt',[a]);self.cli('start',b);t=self.launch(a);self.change(t)
        self.approve(t);self.assertEqual(len(self.calls('run')),1)
        self.call('accept',a,ok=False);self.assertEqual(self.task(a)['state'],'approved')
        self.git('-c','commit.gpgsign=false','merge','--no-ff','-m','fixture merge','one')
        self.cli('accept',a);self.assertEqual(self.task(a)['state'],'merged');self.assertEqual(self.task(b)['state'],'working')
        self.assertEqual(self.git('rev-parse','HEAD',where=self.task(b)['worktree']),self.git('rev-parse','main'))
    def test_no_review_means_no_acceptance(self):
        t=self.launch(self.add());self.change(t);self.call('accept',t['id'],ok=False)
    def test_ready_report_rejects_out_of_scope_changes(self):
        t=self.launch(self.add());self.change(t,'b.txt')
        self.call('report',t['id'],'--state','ready_for_review','--file',self.report,env={'HC_TERMINAL_ID':t['terminal']},ok=False)
        self.assertEqual(self.task(t['id'])['state'],'working');self.assertFalse(self.calls('send'))
    def test_ready_report_rejects_dirty_checkout(self):
        t=self.launch(self.add());(Path(t['worktree'])/'a.txt').write_text('dirty')
        self.call('report',t['id'],'--state','ready_for_review','--file',self.report,env={'HC_TERMINAL_ID':t['terminal']},ok=False)
    def test_report_is_saved_and_delivered_once_without_force(self):
        t=self.launch(self.add());r=self.report_as(t,'question');self.report_as(t,'question')
        self.assertTrue(Path(r['report']).exists());self.assertEqual(r['delivery'],'delivered');self.assertEqual(len(self.calls('send')),1)
        self.assertNotIn('--force',self.calls('send')[0]);self.assertEqual(self.task(t['id'])['state'],'question')
    def test_reporter_must_be_actual_worker(self):
        t=self.launch(self.add());self.call('report',t['id'],'--state','question','--file',self.report,ok=False);self.assertFalse(self.calls('send'))
    def test_delivery_failure_is_retained_without_blind_duplicate(self):
        t=self.launch(self.add());r=self.report_as(t,'question',HC_SEND_FAIL='1');self.assertIn('uncertain',r['delivery'])
        self.report_as(t,'question');self.cli('deliver');self.assertEqual(len(self.calls('send')),1)
        self.cli('deliver','--retry-uncertain');self.assertEqual(len(self.calls('send')),2)
    def test_no_automatic_send_to_owners_shell(self):
        t=self.launch(self.add());db=self.db();db['boss'].update(status=None,argv=['/bin/bash']);self.db(db)
        r=self.report_as(t,'question');self.assertEqual(r['delivery'],'pending');self.assertFalse(self.calls('send'))
    def test_stale_agent_status_on_restored_shell_cannot_receive_report(self):
        t=self.launch(self.add());db=self.db();db['boss'].update(pid=123,foreground={'pid':123,'argv':['/bin/bash']},argv=['/bin/bash'])
        self.db(db);r=self.report_as(t,'question');self.assertEqual(r['delivery'],'pending');self.assertFalse(self.calls('send'))
    def test_recovery_leaves_live_agent_alone(self):
        t=self.launch(self.add());self.cli('recover','--apply');self.call('resume',t['id'],ok=False);self.assertEqual(len(self.calls('run')),1)
    def test_recovery_disconnect_does_not_mark_lost(self):
        t=self.launch(self.add());self.call('recover','--apply',env={'HC_LS_FAIL':'1'},ok=False);self.assertEqual(self.task(t['id'])['state'],'working')
    def test_resume_retains_existing_checkout_commits_and_dirty_files(self):
        t=self.launch(self.add());head=self.change(t);(Path(t['worktree'])/'a.txt').write_text('unfinished\n');self.finish_terminal(t)
        self.cli('recover');self.assertEqual(self.task(t['id'])['state'],'working')
        self.cli('recover','--apply');self.assertEqual(self.task(t['id'])['state'],'lost');self.cli('resume',t['id'])
        n=self.task(t['id']);self.assertEqual(n['worktree'],t['worktree']);self.assertEqual(self.git('rev-parse','HEAD',where=n['worktree']),head)
        self.assertEqual((Path(n['worktree'])/'a.txt').read_text(),'unfinished\n');self.assertEqual(len(self.calls('run')),2)
    def test_ambiguous_launch_preserves_checkout_and_recovery_reattaches(self):
        key=self.add();self.call('start',key,env={'HC_EMPTY_RUN':'1'},ok=False);t=self.task(key)
        self.assertEqual(t['state'],'lost');self.assertTrue(Path(t['worktree']).is_dir());self.assertEqual(len(self.calls('run')),1)
        self.cli('recover','--apply');self.assertEqual(self.task(key)['state'],'working');self.call('resume',key,ok=False);self.assertEqual(len(self.calls('run')),1)
    def test_setup_failure_rolls_back_new_checkout_but_keeps_task(self):
        key=self.add();self.call('start',key,env={'WT_SETUP':'false'},ok=False)
        self.assertEqual(self.task(key)['state'],'failed');self.assertFalse(self.calls('run'));self.assertFalse((self.home/'.herdr/worktrees/fixture/feature').exists())
    def test_failed_git_creation_does_not_delete_a_branch_created_by_someone_else(self):
        key=self.add();real_git=shutil.which('git')
        shim=self.root/'bin/git'
        shim.write_text('#!/usr/bin/env python3\nimport subprocess,sys\na=sys.argv[1:]\nif "worktree" in a and "add" in a:\n subprocess.check_call('+repr([real_git,'-C',str(self.repo),'branch','feature'])+')\n sys.exit(1)\nsys.exit(subprocess.call(['+repr(real_git)+',*a]))\n');shim.chmod(0o755)
        self.call('start',key,ok=False)
        self.assertEqual(self.git('rev-parse','feature'),self.git('rev-parse','main'));self.assertFalse(self.calls('run'))
    def test_corrupt_registry_is_not_reset(self):
        key=self.add();p=self.root/'durable/tasks.json';p.write_text('{broken')
        self.call('list',ok=False);self.assertEqual(p.read_text(),'{broken')
    def test_concurrent_adds_preserve_every_task(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            keys=list(pool.map(lambda n:self.add('feature'+str(n),'a.txt'),range(8)))
        self.assertEqual(len(set(keys)),8);self.assertEqual(len(self.cli('list')),8)
    def test_concurrent_starts_respect_capacity(self):
        self.env['MAX_PARALLEL_WORKERS']='1';a=self.add('one');b=self.add('two','b.txt')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda key:self.cli('start',key),[a,b]))
        self.assertEqual(len(self.calls('run')),1)
    def test_other_coordinators_queue_does_not_block_dispatch(self):
        db=self.db();db['other']={**db['boss'],'address':'other','name':'other'};self.db(db)
        a=self.add('one');b=self.add('two','b.txt',[a],owner='other');self.cli('start',b,env={'HC_TERMINAL_ID':'other'})
        self.launch(a);self.assertEqual(self.task(a)['state'],'working');self.assertEqual(self.task(b)['state'],'queued')
    def test_legacy_worker_can_be_adopted_without_restarting_it(self):
        wt=self.root/'legacy';self.git('worktree','add','-qb','legacy',str(wt),'main')
        db=self.db();db['legacy-worker']={'address':'legacy-worker','name':'legacy-worker','parent':'boss','state':'live','status':{'state':'idle','app':'claude-code'},'labels':{}};self.db(db)
        self.cli('list');runtime=self.home/'.cache/leha-loop/fixture/state.json'
        runtime.write_text(json.dumps({'legacy':{'worker':'legacy-worker','term':'legacy-worker','parent':'boss','wt':str(wt),'port':'4110','phase':'working','task':str(self.brief)}}))
        key=self.add();self.cli('start',key);self.assertFalse(self.calls('run'))
        adopted=self.cli('adopt','legacy','--owns','b.txt');self.assertEqual(adopted['terminal'],'legacy-worker');self.assertFalse(self.calls('run'))
        self.cli('schedule');self.assertEqual(len(self.calls('run')),1)
    def test_cache_loss_does_not_lose_registry(self):
        t=self.launch(self.add());shutil.rmtree(self.home/'.cache/leha-loop');self.assertEqual(self.task(t['id'])['terminal'],t['terminal'])
    def test_cache_loss_does_not_reuse_another_tasks_reserved_port(self):
        a=self.launch(self.add('one'));shutil.rmtree(self.home/'.cache/leha-loop')
        b=self.launch(self.add('two','b.txt'));self.assertNotEqual(a['port'],b['port'])
    def test_failed_live_worker_keeps_ownership_until_explicit_resolution(self):
        t=self.launch(self.add());self.report_as(t,'failed');self.call('cancel',t['id'],ok=False)
        b=self.add('second');self.cli('start',b);self.assertEqual(self.task(b)['state'],'queued')
    def test_stale_approved_sha_cannot_be_merged_or_accepted(self):
        t=self.launch(self.add());self.change(t);self.approve(t);self.change(t,body='later\n')
        self.call('feature',script='merge.sh',ok=False);self.call('accept',t['id'],ok=False)
    def test_invalid_scope_and_dependency_are_rejected(self):
        for scope in ['../outside','/tmp/outside','src/*']:
            self.call('add','--branch','feature','--name','worker','--brief',self.brief,'--owns',scope,ok=False)
        self.call('add','--branch','feature','--name','worker','--brief',self.brief,'--owns','a.txt','--depends-on','T9999',ok=False)
    def test_cancel_queued_task_never_kills_any_terminal(self):
        key=self.add();self.cli('cancel',key);self.assertEqual(self.task(key)['state'],'cancelled');self.assertFalse(self.calls('run'));self.assertFalse(self.calls('kill'))
    def test_scope_change_is_coordinator_only_and_respects_active_ownership(self):
        a=self.launch(self.add('one'));b=self.launch(self.add('two','b.txt'))
        self.call('scope',a['id'],'--owns','b.txt',ok=False)
        self.call('scope',a['id'],'--owns','c.txt',env={'HC_TERMINAL_ID':a['terminal']},ok=False)
        self.cli('scope',a['id'],'--owns','c.txt');self.assertEqual(self.task(a['id'])['owns'],['c.txt'])
    def test_revoked_review_cannot_be_accepted_after_merge(self):
        t=self.launch(self.add());head=self.change(t);self.approve(t)
        self.git('-c','commit.gpgsign=false','merge','--no-ff','-m','fixture merge','feature')
        (self.home/'.cache/leha-loop/fixture/reviews/review-feature.md').write_text('feature@'+head+': CHANGES REQUESTED — revoked\n')
        self.call('accept',t['id'],ok=False);self.assertEqual(self.task(t['id'])['state'],'approved')
    def test_kept_shell_can_resume_but_unknown_live_program_cannot(self):
        t=self.launch(self.add());db=self.db();db[t['terminal']].update(status=None,argv=['/bin/bash']);self.db(db)
        self.cli('recover','--apply');self.assertEqual(self.task(t['id'])['state'],'lost');self.cli('resume',t['id']);self.assertEqual(len(self.calls('run')),2)
    def test_unknown_live_program_is_not_replaced(self):
        t=self.launch(self.add());db=self.db();db[t['terminal']].update(status=None,argv=['/usr/bin/vim']);self.db(db)
        self.cli('recover','--apply');self.call('resume',t['id'],ok=False);self.assertEqual(len(self.calls('run')),1)
    def test_direct_launch_cannot_bypass_capacity_or_create_duplicate_queued_task(self):
        self.env['MAX_PARALLEL_WORKERS']='1';self.launch(self.add())
        self.call('direct','direct-worker',self.brief,script='mkworker.sh',ok=False)
        ts=self.cli('list');self.assertEqual(len(ts),2);self.assertEqual(ts[1]['state'],'queued');self.assertFalse((self.home/'.herdr/worktrees/fixture/direct').exists())
        self.call('direct','direct-worker',self.brief,script='mkworker.sh',ok=False);self.assertEqual(len(self.cli('list')),2);self.assertEqual(len(self.calls('run')),1)
    def test_cancel_lost_task_keeps_worktree_and_never_kills_processes(self):
        t=self.launch(self.add());self.finish_terminal(t);self.cli('recover','--apply');self.cli('cancel',t['id'])
        self.assertTrue(Path(t['worktree']).exists());self.assertFalse(self.calls('kill'));self.assertEqual(self.task(t['id'])['state'],'cancelled')
    def test_parallel_resumes_never_duplicate_a_lost_worker(self):
        t=self.launch(self.add());self.finish_terminal(t);self.cli('recover','--apply')
        def resume(_):return subprocess.run(['bash',str(self.skill/'scripts/task.sh'),'resume',t['id']],cwd=self.repo,env=self.env,text=True,capture_output=True,timeout=30)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(resume,range(2)))
        self.assertEqual(sum(r.returncode==0 for r in results),1);self.assertEqual(len(self.calls('run')),2)
    def test_cancel_cannot_race_a_launch_and_release_live_ownership(self):
        key=self.add()
        def invoke(command):return subprocess.run(['bash',str(self.skill/'scripts/task.sh'),command,key],cwd=self.repo,env=self.env,text=True,capture_output=True,timeout=30)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(invoke,['start','cancel']))
        t=self.task(key)
        if t['state']=='cancelled':self.assertFalse(self.calls('run'))
        else:self.assertEqual(t['state'],'working');self.assertEqual(len(self.calls('run')),1)
    def test_changed_base_does_not_silently_retarget_a_queued_task(self):
        key=self.add();self.git('branch','other-base')
        self.call('start',key,env={'BASE':'other-base'},ok=False);self.assertFalse(self.calls('run'))
        self.assertEqual(self.task(key)['base'],'main')
    def test_runtime_updates_are_atomic_and_do_not_lose_other_branches(self):
        p=self.root/'runtime.json';p.write_text('{}')
        def update(n):subprocess.check_call([sys.executable,str(SOURCE/'state.py'),str(p),'set','b'+str(n),'phase','working'],stdout=subprocess.DEVNULL)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(update,range(16)))
        self.assertEqual(len(json.loads(p.read_text())),16)

if __name__=='__main__':unittest.main()
