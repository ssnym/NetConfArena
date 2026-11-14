import sys, os, re, json
from dotenv import load_dotenv
load_dotenv()

from task.task_loader import render_task_file
from environment.task_environment import TaskEnvironment
from environment.device_profile import sonic_wrap_batch
from evaluator.evaluate_task import Evaluator

ERR = re.compile(r'Error|failed|Exiting|Usage:|^%|not running|Unknown command|Ambiguous|(?<!set origin )incomplete', re.M | re.I)
OK_ERR = re.compile(r'does not exist|not found|No such|already exists', re.I)

def apply_verbose(env, configs, stage_name="configs"):
    bad = False
    for dev, cmds in configs.items():
        if hasattr(env, 'node_current_configs'):
            env.node_current_configs.pop(dev, None)
        for line in sonic_wrap_batch(list(cmds)):
            out = env.send_command_to_node(dev, line) or ""
            if ERR.search(out) and not OK_ERR.search(out):
                bad = True
                print(f"\n!! [{stage_name}] {dev}: {line[:400]}\n{out.strip()[-800:]}")
    return bad

path = sys.argv[1]
seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1

task = render_task_file(path, randomize=True, seed=seed)
env = TaskEnvironment(task_data=task)
try:
    # STAGE 1: Startup Configs
    print("\n===== STAGE 1: APPLYING STARTUP CONFIGS =====")
    startup_cfgs = task.get("startup_configs", {})
    bad_startup = apply_verbose(env, startup_cfgs, "startup")
    if bad_startup and not os.environ.get("CONTINUE"):
        print("\nStartup configs did not apply cleanly. Fix the converter, or set CONTINUE=1 to evaluate anyway.")
        sys.exit(1)
    
    ev1 = Evaluator()
    ev1.evaluate_task(env.evaluator_interface, task)
    res1 = ev1.task_evaluation_result
    startup_score = res1["testcase_execution_results"]["score"]
    print(f"\nstartup_score: {startup_score} (ideal: ~0.0)")
    
    # STAGE 2: Groundtruth Configs
    print("\n===== STAGE 2: APPLYING GROUNDTRUTH CONFIGS =====")
    bad_gt = apply_verbose(env, task["groundtruth_configs"], "groundtruth")
    if bad_gt:
        print("\n[WARNING] Groundtruth had apply collisions/errors, but proceeding to evaluate anyway.")
        
    ev2 = Evaluator()
    ev2.evaluate_task(env.evaluator_interface, task)
    res2 = ev2.task_evaluation_result
    
    passed = res2["testcase_execution_results"]["testcases_passed"]
    gt_score = res2["testcase_execution_results"]["score"]
    print(f"\ngroundtruth_score: {gt_score} (ideal: 1.0)")
    print(json.dumps(passed, indent=2))
    print(json.dumps(res2.get("config_evaluation_results", {}).get("evaluation_result", {}), indent=2, default=str))

    # Raw device output for every failed testcase
    gi = 0
    for g in task["testcases"]:
        if not g.get("testcase"):
            continue
        gi += 1
        for ti, t in enumerate(g["testcase"], 1):
            if passed.get(f"{gi}-{ti}") is False:
                print(f"\n##### RAW {gi}-{ti} {t['node']} $ {t['command']}")
                raw = env.send_command_to_node(t["node"], t["command"]) or ""
                print("\n".join(raw.splitlines()[:30]))
finally:
    if os.environ.get("KEEP_LAB"):
        print("KEEP_LAB set: lab left running (project:", env.project_name, ")")
    else:
        env.close()
