#!/usr/bin/python
"""Observe or compile a scope on the real target; never execute changes."""
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils import mac_setup_engine as engine
from ansible.module_utils import mac_setup_planner as planner


def main():
    module = AnsibleModule(argument_spec={
        "config": {"type": "dict", "required": True, "no_log": True},
        "operation": {"type": "str", "choices": ["setup", "update", "finish"], "default": "setup"},
        "mode": {"type": "str", "choices": ["observe", "compile"], "default": "observe"},
        "review_digest": {"type": "str", "default": ""},
    }, supports_check_mode=True)
    try:
        config = planner.validate_config(module.params["config"])
        # Invalid/unknown input remains redacted. Only this closed public schema
        # is safe to return; retaining scalar redaction would corrupt hashes and
        # versions (for example schema_version=1 redacts every digit '1').
        module.no_log_values.clear()
        if module.params["mode"] == "compile":
            result = engine.compile_operations(config, module.params["operation"], module.params["review_digest"])
        else:
            result = engine.observe(config, module.params["operation"])
            result["prerequisite_config"] = engine.prerequisite_scope(config, result)
        module.exit_json(changed=False, scope=result)
    except (engine.EngineError, planner.ConfigError) as exc:
        module.fail_json(msg=str(exc))
    except Exception:
        module.fail_json(msg="Target observations could not be completed safely; no mutations performed.")


if __name__ == "__main__":
    main()
