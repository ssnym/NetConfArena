import time
from .telnet_node import run_parallel
from .utils import remove_redundant_prompt, clean_configuration, remove_banner_sections, extract_prompt, ensure_start_end

class AgentInterface:
    def __init__(self, parent):
        self.parent = parent  # Reference to the owning environment instance.
    
    def get_current_config(self, node_names):
        result = {
            "status": "Success",
            "observation": {}
            }

        def worker(node_name):
            cfg = self.parent.node_current_configs.get(node_name)
            if cfg != None and cfg.get("is_set") == True:
                return cfg["running-config"]

            tn = self.parent._health_check_node(node_name)
            if not tn:
                return ""
            self.parent.send_command_to_node(node_name, "enable")
            running_config = self.parent.send_command_to_node(node_name, "show running-config")
            running_config = remove_redundant_prompt(running_config)
            running_config = clean_configuration(remove_banner_sections(running_config))
            self.parent.node_current_configs[node_name] = {"is_set" : True, "running-config" : running_config}
            return running_config

        # Each node is independent — read every node's config in parallel.
        outputs = run_parallel(node_names, worker)
        for node_name, running_config in outputs.items():
            if running_config != "":
                result["observation"].update({node_name: running_config})

        return result
        
    def apply_config(self, config_commands):
        result = {
            "status": "Success",
            "observation": {}
            }

        def worker(node_name):
            cfg = self.parent.node_current_configs.get(node_name)
            if cfg != None and cfg["is_set"] == True:
                cfg["is_set"] = False

            node_commnads_output = ""
            command_result = ""
            tn = self.parent._health_check_node(node_name)

            if tn:
                self.parent.send_command_to_node(node_name, "enable")
                commands = ensure_start_end(config_commands[node_name].split("\n"), "configure terminal", "end")
                for command in commands:
                    command_result += self.parent.send_command_to_node(node_name, command)

                prompt = extract_prompt(command_result)
                command_result = prompt + remove_redundant_prompt(command_result)
                node_commnads_output += command_result

            return node_commnads_output

        # Apply each node's config in parallel; commands stay ordered per node.
        outputs = run_parallel(list(config_commands.keys()), worker)
        for node_name, node_commnads_output in outputs.items():
            result["observation"].update({node_name: node_commnads_output})
        self.parent.last_update_time = time.time()

        return result
    
    def execute_validation(self, validation_commands):
        result = {
            "status": "Success",
            "observation": {}
            }

        def worker(node_name):
            node_commnads_output = ""
            command_result = ""
            tn = self.parent._health_check_node(node_name)

            if tn:
                for command in validation_commands[node_name].split("\n"):
                    if not command.startswith("show") and not command.startswith("ping") and not command.startswith("traceroute"):
                        return "You are only allowed to use validation commands such as show, ping, and traceroute."
                    command_result += self.parent.send_command_to_node(node_name, command)

                prompt = extract_prompt(command_result)
                command_result = prompt + remove_redundant_prompt(command_result)
                node_commnads_output += command_result

            return node_commnads_output

        # Run each node's validation commands in parallel.
        outputs = run_parallel(list(validation_commands.keys()), worker)
        for node_name, node_commnads_output in outputs.items():
            result["observation"].update({node_name: node_commnads_output})

        return result

    def wait_for_stabilization(self, seconds):
        time.sleep(seconds)
        result = {
            "status": "Success",
            "observation": f"Waited {seconds} seconds for stabilization"
        }
        return result

    def submit(self):
        result = {
            "status": "Finished"
        }

        return result

    # --- Action aliases -----------------------------------------------------
    # Public five-action API: get_running_config / apply_config /
    # execute_validation / wait / submit. The aliases below map those names to
    # the internal implementations. The dispatcher resolves actions by
    # attribute lookup (see agent/react.py:call_agent_network_interface).
    def get_running_config(self, node_names):
        return self.get_current_config(node_names)

    def wait(self, seconds):
        return self.wait_for_stabilization(seconds)
