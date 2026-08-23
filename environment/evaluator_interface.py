from .utils import remove_redundant_prompt, clean_command_output

class EvaluatorInterface:
    def __init__(self, parent):
        self.parent = parent  # Reference to the owning environment instance.
        self.node_startup_configs = {}

    def get_all_node(self):
        all_node = []
        
        for node in self.parent.get_connections():
            all_node.append(node)

        return all_node

    def get_all_node_current_configs(self):
        return self.parent.get_all_node_current_configs()
    
    def execute_command(self, node_name, command):
        output = self.parent.send_command_to_node(node_name, command)
        output = remove_redundant_prompt(output)
        output = clean_command_output(command, output)
        return output
