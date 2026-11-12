#!/usr/bin/env python3
"""Hermetic launcher tests: real temporary git worktrees, fake hc and app layout. No agents or user projects."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parent
COMMON = r'''
FB=$FIXTURE
REPO=$FIXTURE/repo
WT_ROOT=$FIXTURE/worktrees
STATE=$FIXTURE/state
REVIEWS=$STATE/reviews
STATE_FILE=$STATE/state.json
BASE=main
PREFIX=test-
PROJ=test
BOSS=${HC_TERMINAL_ID:-}
SPACE=caller-space
WORKER_PORTS=3100-3101
WORKER_CMD=(fake-worker --model test)
REVIEWER_CMD=(fake-reviewer --no-daemon)
COPY=()
WT_SETUP=""
mkdir -p "$STATE" "$REVIEWS"
[ -s "$STATE_FILE" ] || echo '{}' > "$STATE_FILE"
fb_die() { echo "ERROR: $*" >&2; exit 1; }
fb_launch_locked() { :; }
fb_task_reserve() { echo fixture-task; }
fb_task_phase() { :; }
fb_task() { :; }
fb_live() { return 1; }
fb_port_busy() { return 1; }
fb_render() { echo 'Fixture rules'; }
fb_accept_trust() { return 0; }
fb_cwd_branch() { git rev-parse --abbrev-ref HEAD; }
fb_state_get() { jq -r --arg b "$1" --arg k "$2" '.[$b][$k] // empty' "$STATE_FILE"; }
fb_state_set() {
  python3 - "$STATE_FILE" "$@" <<'PY'
import json,sys
p,b,*kv=sys.argv[1:]
d=json.load(open(p)); d.setdefault(b,{}).update(dict(zip(kv[::2],kv[1::2])))
with open(p,'w') as f: json.dump(d,f)
PY
}
fb_label() { hc label "$1" "herdr.space=$2" "herdr.tab=$3" "where=$4"; }
fb_same_commit() { [ "$(git -C "$REPO" rev-parse "$1")" = "$(git -C "$REPO" rev-parse "$2")" ]; }
fb_reviewer_model() { echo fixture-model; }
fb_report_verdict() { return 0; }
'''
HC = r'''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
with open(Path(os.environ['FIXTURE'])/'calls.jsonl','a') as f:f.write(json.dumps(['hc',*args])+'\n')
if args[0]=='get':
 t=args[1]
 if t in ('boss-alias','t-boss','t-worker') or (t=='test-review-feature' and os.environ.get('OLD_REVIEWER')=='1'):
  address='t-boss' if t=='boss-alias' else 't-old-reviewer' if t=='test-review-feature' else t
  state=os.environ.get('WORKER_STATE' if t=='t-worker' else 'BOSS_STATE','live')
  labels={'herdr.space':'worker-space' if t=='t-worker' else 'boss-space','herdr.tab':'worker-tab'}
  print(json.dumps({'address':address,'state':state,'labels':labels,'status':{'state':'working'}}))
 else:sys.exit(3)
elif args[0]=='run':print(json.dumps({'address':'t-new'}))
else:print('{}')
'''
LAYOUT = r'''import sys,json,os
from pathlib import Path
with open(Path(os.environ['FIXTURE'])/'calls.jsonl','a') as f:f.write(json.dumps(['layout',*sys.argv[1:]])+'\n')
print('work-space work-tab' if sys.argv[1]=='add-space' else 'ok')
'''

class NativeParentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='leha-native-test-')
        self.root=Path(self.tmp.name)
        for d in ('scripts','bin','repo','state/reviews'): (self.root/d).mkdir(parents=True)
        for name in ('mkworker.sh','review.sh'): shutil.copy2(SOURCE/name,self.root/'scripts'/name)
        (self.root/'scripts/common.sh').write_text(COMMON)
        (self.root/'scripts/layout.py').write_text(LAYOUT)
        (self.root/'bin/hc').write_text(HC);(self.root/'bin/hc').chmod(0o755)
        self.env={**os.environ,'FIXTURE':str(self.root),'PATH':str(self.root/'bin')+os.pathsep+os.environ['PATH'],'HC_TERMINAL_ID':'boss-alias','GIT_CONFIG_GLOBAL':os.devnull,'GIT_CONFIG_SYSTEM':os.devnull}
        for k in ('FB_PARENT','BOSS_STATE','WORKER_STATE','OLD_REVIEWER'):self.env.pop(k,None)
        self.git('init','-q','-b','main')
        (self.root/'repo/file.txt').write_text('test\n')
        self.git('add','file.txt')
        self.git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','-c','commit.gpgsign=false','commit','-qm','fixture')
        (self.root/'task.md').write_text('Fixture task')
    def tearDown(self): self.tmp.cleanup()
    def git(self,*args):
        return subprocess.check_output(['git','-C',str(self.root/'repo'),*args],env=self.env,text=True,stderr=subprocess.STDOUT).strip()
    def run_script(self,name,*args):
        return subprocess.run(['bash',str(self.root/'scripts'/name),*map(str,args)],env=self.env,cwd=self.root/'repo',text=True,capture_output=True,timeout=15)
    def calls(self):
        p=self.root/'calls.jsonl'
        return [json.loads(s) for s in p.read_text().splitlines()] if p.exists() else []
    def launch(self,name='mkworker.sh'):
        return next(c for c in self.calls() if c[:2]==['hc','run'])
    def prepare_review(self,term='t-worker'):
        wt=self.root/'worktrees/feature';self.git('worktree','add','-qb','feature',str(wt),'main')
        (self.root/'state/state.json').write_text(json.dumps({'feature':{'term':term,'wt':str(wt),'review_ok_sha':self.git('rev-parse','feature'),'rounds':'0'}}))
        return wt
    def test_worker_native_parent_and_own_worktree_space(self):
        r=self.run_script('mkworker.sh','feature','worker',self.root/'task.md');self.assertEqual(r.returncode,0,r.stderr)
        call=self.launch();self.assertEqual(call[call.index('--parent')+1],'t-boss');self.assertIn('--outlive-parent',call);self.assertNotIn('--no-parent',call)
        self.assertEqual(call[call.index('--cwd')+1],str(self.root/'worktrees/feature'))
        self.assertIn('leha.role=worker',call);self.assertIn('leha.branch=feature',call)
        self.assertTrue(any(c[:2]==['layout','add-space'] for c in self.calls()))
        self.assertTrue(any('herdr.space=work-space' in c and 'herdr.tab=work-tab' in c for c in self.calls()))
        self.assertEqual(json.loads((self.root/'state/state.json').read_text())['feature']['parent'],'t-boss')
    def test_missing_parent_fails_before_creating_worktree(self):
        self.env.pop('HC_TERMINAL_ID');r=self.run_script('mkworker.sh','feature','worker',self.root/'task.md')
        self.assertNotEqual(r.returncode,0);self.assertFalse((self.root/'worktrees/feature').exists());self.assertFalse(any(c[:2]==['hc','run'] for c in self.calls()))
    def test_finished_boss_fails_before_creating_worktree(self):
        self.env['BOSS_STATE']='finished';r=self.run_script('mkworker.sh','feature','worker',self.root/'task.md')
        self.assertNotEqual(r.returncode,0);self.assertFalse((self.root/'worktrees/feature').exists())
    def test_explicit_external_parent_is_supported(self):
        self.env.pop('HC_TERMINAL_ID');self.env['FB_PARENT']='t-boss'
        r=self.run_script('mkworker.sh','feature','worker',self.root/'task.md');self.assertEqual(r.returncode,0,r.stderr)
        call=self.launch();self.assertEqual(call[call.index('--parent')+1],'t-boss')
    def test_reviewer_parent_is_recorded_worker_not_caller_boss(self):
        wt=self.prepare_review();r=self.run_script('review.sh','feature');self.assertEqual(r.returncode,0,r.stderr)
        call=self.launch();self.assertEqual(call[call.index('--parent')+1],'t-worker');self.assertIn('--outlive-parent',call);self.assertNotIn('--no-parent',call)
        self.assertEqual(call[call.index('--cwd')+1],str(wt));self.assertIn('leha.role=reviewer',call)
        self.assertTrue(any(c[:3]==['layout','add-right','t-worker'] for c in self.calls()))
        self.assertTrue(any('herdr.space=worker-space' in c for c in self.calls()))
    def test_replacement_reviewer_keeps_existing_pane(self):
        self.prepare_review();self.env['OLD_REVIEWER']='1'
        r=self.run_script('review.sh','feature');self.assertEqual(r.returncode,0,r.stderr)
        call=self.launch();self.assertEqual(call[call.index('--parent')+1],'t-worker')
        self.assertTrue(any(c[:4]==['layout','swap','t-old-reviewer','t-new'] for c in self.calls()))
    def test_dead_worker_preserves_previous_reviewer_and_report(self):
        self.prepare_review();self.env['WORKER_STATE']='finished';self.env['OLD_REVIEWER']='1'
        report=self.root/'state/reviews/review-feature.md';report.write_text('Keep this report')
        r=self.run_script('review.sh','feature');self.assertNotEqual(r.returncode,0)
        self.assertEqual(report.read_text(),'Keep this report')
        self.assertFalse(any(c[:2] in (['hc','kill'],['hc','rm'],['hc','run']) for c in self.calls()))
    def test_missing_worker_does_not_fall_back_to_boss(self):
        self.prepare_review('');r=self.run_script('review.sh','feature');self.assertNotEqual(r.returncode,0)
        self.assertFalse(any(c[:2]==['hc','run'] for c in self.calls()))
    def test_merger_and_dev_server_remain_independent(self):
        source=(SOURCE/'team-up.sh').read_text();self.assertIn('--no-parent --outlive-parent --keep',source)

if __name__=='__main__':unittest.main()
