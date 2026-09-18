"""Execute the real Ansible role/module against temporary fake provider programs.

Only the provider I/O boundary and destination constants are substituted in a
temporary module copy. Resolver, compiler, digest checks, module packaging and
the role's actual sequential dispatch remain unchanged. No live package commands.
"""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]

FAKE_PROVIDER = r'''#!PYTHON
import json,sys
from pathlib import Path
state_file=Path(STATE_PATH)
log_file=Path(LOG_PATH)
data=json.loads(state_file.read_text())
args=sys.argv[1:]
with log_file.open('a') as out: out.write(json.dumps(args)+'\n')
if args[:2]==['info','--json=v2']:
    name=args[-1]; item=data['packages'][name]
    print(json.dumps({'formulae':[{'name':name,'installed':[{'version':item['version']}] if item['present'] else [],'pinned':item['held'],'versions':{'stable':item['candidate']}}]}))
elif args[:1]==['fixture-metadata']:
    if args[1].startswith('https://registry.npmjs.org/'):
        print(json.dumps({'version':'3.6.2'})); sys.exit(0)
    name=args[1].rsplit('/',1)[-1][:-5]; item=data['packages'][name]
    print(json.dumps({'versions':{'stable':item['candidate']},'disabled':False,'dependencies':[]}))
elif args[:1]==['deps'] or args[:3]==['uses','--installed','--recursive']:
    print('')
elif args[:1]==['--prefix']:
    print(str(Path(__file__).parent / 'prefix/opt' / args[-1]))
elif args[:1] and args[0] in ('install','upgrade','pin'):
    name=args[-1]
    if name==data.get('fail'): sys.exit(42)
    if name not in data['packages']: sys.exit(98)
    item=data['packages'][name]
    if args[0]=='pin': item['held']=True
    else: item.update(present=True,version=item['candidate'])
    data['mutations'].append(args)
    state_file.write_text(json.dumps(data))
    print('synthetic change')
else:
    print('unrecognized fake provider request',file=sys.stderr); sys.exit(99)
'''

CALLBACK = '''from ansible.plugins.callback import CallbackBase
import json
class CallbackModule(CallbackBase):
    CALLBACK_VERSION=2.0
    CALLBACK_TYPE='stdout'
    CALLBACK_NAME='qa_json'
    def v2_runner_on_ok(self,result):
        self._display.display(json.dumps({'event':'ok','task':result._task.get_name(),'result':result._result},default=str))
    def v2_runner_on_failed(self,result,ignore_errors=False):
        self._display.display(json.dumps({'event':'failed','task':result._task.get_name(),'result':result._result},default=str))
    def v2_playbook_on_stats(self,stats):
        self._display.display(json.dumps({'event':'stats','hosts':{host:stats.summarize(host) for host in stats.processed}}))
'''


class NativeExecutionTests(unittest.TestCase):
    def setUp(self):
        self.ansible = shutil.which("ansible-playbook")
        self.assertIsNotNone(self.ansible, "Ansible is required; this suite never installs it")
        self.temp = tempfile.TemporaryDirectory(prefix="public-native-qa-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script_python = self.root / "fixture-python"
        self.script_python.symlink_to(sys.executable)
        self.engine_root = self.root / "engine"
        self.engine_root.mkdir()
        self.state_path = self.root / "state.json"
        self.command_log = self.root / "provider.log"
        self.fake_brew = self.root / "fake-brew"
        self.fake_brew.write_text(FAKE_PROVIDER.replace("PYTHON", str(self.script_python), 1)
                                  .replace("STATE_PATH", repr(str(self.state_path)))
                                  .replace("LOG_PATH", repr(str(self.command_log))))
        self.fake_brew.chmod(0o755)
        self.home = self.root / "home"
        self.home.mkdir()
        for folder in ("module_utils", "library", "roles"):
            shutil.copytree(ROOT / folder, self.engine_root / folder)
        self.substitute_provider_boundary()
        callback = self.root / "callback_plugins"
        callback.mkdir()
        (callback / "qa_json.py").write_text(CALLBACK)
        self.ansible_config = self.root / "ansible.cfg"
        self.ansible_config.write_text(
            "[defaults]\nstdout_callback=qa_json\nretry_files_enabled=False\n"
            "host_key_checking=True\ngathering=explicit\n"
            f"callback_plugins={callback}\nlibrary={self.engine_root / 'library'}\n"
            f"module_utils={self.engine_root / 'module_utils'}\n"
            f"roles_path={self.engine_root / 'roles'}\n")
        self.inventory = self.root / "inventory"
        self.inventory.write_text(f"localhost ansible_connection=local ansible_python_interpreter={json.dumps(sys.executable)}\n")
        self.write_state({"git": False})

    def substitute_provider_boundary(self):
        path = self.engine_root / "module_utils/mac_setup_engine.py"
        text = path.read_text()
        lines = text.splitlines(keepends=True)
        tree = ast.parse(text)
        replacements = []
        constants = {"BREW": repr(str(self.fake_brew)), "PREFIX": repr(str(self.root / "prefix")),
                     "HOME": "Path(" + repr(str(self.home)) + ")",
                     "PUBLIC": "Path(" + repr(str(self.home / 'public')) + ")"}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name in constants:
                    replacements.append((node.lineno - 1, node.end_lineno, name + " = " + constants.pop(name) + "\n"))
            if isinstance(node, ast.ClassDef) and node.name == "ProductionReader":
                methods = [n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "get_json"]
                self.assertEqual(len(methods), 1)
                method = methods[0]
                replacements.append((method.lineno - 1, method.end_lineno,
                                     "    def get_json(self, url):\n        return parsed_json(self.run([BREW, 'fixture-metadata', url]))\n"))
        self.assertFalse(constants, "review changed provider constants before fixture execution")
        self.assertEqual(len(replacements), 5, "must substitute exactly constants and network I/O boundary")
        for start, end, replacement in sorted(replacements, reverse=True):
            lines[start:end] = [replacement]
        path.write_text("".join(lines))

    def write_state(self, packages, fail=None):
        self.state_path.write_text(json.dumps({"packages": {
            name: {"present": present, "version": "1.0.0", "candidate": "2.0.0", "held": False}
            for name, present in packages.items()}, "fail": fail, "mutations": []}))

    def state(self):
        return json.loads(self.state_path.read_text())

    def run_role(self, selected, mode="observe", digest=None, extra=None):
        variables = {"mac_setup_config": {"schema_version": 1, "profile": "dev", "packages": {"formulae": selected}},
                     "mac_setup_operation": "setup", "mac_setup_mode": mode}
        if digest is not None:
            variables["mac_setup_review_digest"] = digest
        variables.update(extra or {})
        playbook = self.root / "play.yml"
        playbook.write_text(yaml.safe_dump([{"name": "Synthetic native execution", "hosts": "all", "gather_facts": False,
                                            "become": False, "roles": ["mac_setup"],
                                            "post_tasks": [{"name": "QA scope", "ansible.builtin.debug": {"msg": "{{ mac_setup_observation }}"}}]}]))
        env = {"PATH": os.environ["PATH"], "HOME": str(self.home), "ANSIBLE_CONFIG": str(self.ansible_config),
               "ANSIBLE_LOCAL_TEMP": str(self.root / "ansible-local"), "ANSIBLE_REMOTE_TEMP": str(self.root / "ansible-remote"),
               "PYTHONDONTWRITEBYTECODE": "1", "ANSIBLE_NOCOLOR": "1"}
        result = subprocess.run([self.ansible, str(playbook), "-i", str(self.inventory), "-e", json.dumps(variables)],
                                env=env, cwd=self.root, capture_output=True, text=True, timeout=90)
        events = []
        for line in result.stdout.splitlines():
            if line.startswith("{"):
                events.append(json.loads(line))
        return result, events

    def observed(self, selected):
        result, events = self.run_role(selected)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        scopes = [e["result"]["msg"] for e in events if e.get("task") == "QA scope"]
        self.assertEqual(len(scopes), 1, result.stdout)
        self.assertRegex(scopes[0]["digest"], r"^[0-9a-f]{64}$", "native response must preserve its exact review digest")
        return scopes[0]

    def apply(self, selected):
        scope = self.observed(selected)
        result, events = self.run_role(selected, "apply", scope["digest"])
        return result, events

    def test_actual_native_zip_and_command_dispatch_install_then_noop(self):
        first, events = self.apply({"git": {}})
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertTrue(self.state()["packages"]["git"]["present"])
        self.assertEqual(len(self.state()["mutations"]), 1)
        second, events = self.apply({"git": {}})
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(len(self.state()["mutations"]), 1)
        statistics = [e for e in events if e["event"] == "stats"][-1]
        self.assertEqual(statistics["hosts"]["localhost"]["changed"], 0)

    def test_provider_failure_stops_later_operations_and_rerun_only_retries_remaining(self):
        self.write_state({"git": False, "jq": False, "ripgrep": False}, fail="jq")
        selected = {"git": {}, "jq": {}, "ripgrep": {}}
        result, _ = self.apply(selected)
        self.assertNotEqual(result.returncode, 0)
        state = self.state()
        self.assertTrue(state["packages"]["git"]["present"])
        self.assertFalse(state["packages"]["jq"]["present"])
        self.assertFalse(state["packages"]["ripgrep"]["present"], "failure must stop subsequent package operations")
        state["fail"] = None
        self.state_path.write_text(json.dumps(state))
        result, _ = self.apply(selected)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([args[-1] for args in self.state()["mutations"]], ["git", "jq", "ripgrep"])

    def test_direct_apply_requires_review_digest_before_provider_inspection(self):
        result, _ = self.run_role({"git": {}}, "apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.command_log.exists())
        self.assertEqual(self.state()["mutations"], [])

    def test_direct_role_cannot_accept_injected_operations(self):
        scope = self.observed({"git": {}})
        forged = {"operations": [{"provider": "formulae", "id": "git", "argv": [str(self.fake_brew), "install", "--formula", "git"], "environment": {}}]}
        result, _ = self.run_role({"git": {}}, "apply", scope["digest"], {"mac_setup_checked": forged})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state()["mutations"], [])

    def test_changed_target_observation_rejects_stale_scope_without_mutation(self):
        scope = self.observed({"git": {}})
        state = self.state()
        state["packages"]["git"]["candidate"] = "3.0.0"
        self.state_path.write_text(json.dumps(state))
        result, _ = self.run_role({"git": {}}, "apply", scope["digest"])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state()["mutations"], [])

    def test_native_npm_runtime_and_owned_path_block_repeat_without_private_overwrite(self):
        self.write_state({"node@22": True})
        state = self.state()
        state["packages"]["node@22"].update(version="22.1.0", candidate="22.1.0")
        state["npm"] = {}
        self.state_path.write_text(json.dumps(state))
        node = self.root / "prefix/opt/node@22/bin/node"
        node.parent.mkdir(parents=True)
        node.write_text("#!" + str(self.script_python) + "\n" +
            "import json,sys\nfrom pathlib import Path\np=Path(" + repr(str(self.state_path)) + ")\nd=json.loads(p.read_text())\na=sys.argv[1:]\n" +
            "if a==['--version']: print('v22.1.0')\n" +
            "elif a[1:2]==['list']: print(json.dumps({'dependencies':{n:{'version':v} for n,v in d['npm'].items()}}))\n" +
            "elif a[1:2]==['install']:\n n,v=a[-1].rsplit('@',1);d['npm'][n]=v;d['mutations'].append(a);p.write_text(json.dumps(d))\n" +
            "else: sys.exit(98)\n")
        node.chmod(0o755)
        profile = self.home / ".zprofile"
        canary = b"# preserve unrelated private shell bytes\nexport EXISTING_FIXTURE=unchanged\n"
        profile.write_bytes(canary)
        selected = {"node@22": {}}
        extra = {"mac_setup_config": {"schema_version": 1, "profile": "dev", "packages": {"formulae": selected, "npm": {"prettier": {"version": "3.6.2"}}}}}
        for run in range(2):
            result, events = self.run_role(selected, extra=extra)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            scope = [e["result"]["msg"] for e in events if e.get("task") == "QA scope"][0]
            self.assertRegex(scope["digest"], r"^[a-f0-9]{64}$")
            result, events = self.run_role(selected, "apply", scope["digest"], extra)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            if run:
                self.assertEqual([e for e in events if e["event"] == "stats"][-1]["hosts"]["localhost"]["changed"], 0)
        self.assertEqual(self.state()["npm"], {"prettier": "3.6.2"})
        self.assertEqual(len(self.state()["mutations"]), 1)
        self.assertTrue(profile.read_bytes().startswith(canary))
        self.assertEqual(profile.read_bytes().count(b"# BEGIN MACSETUP PUBLIC TOOLS"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
