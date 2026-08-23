import gns3fy
import json
import os
from logs.mylogger import logger

GNS3_CONNECTOR = gns3fy.Gns3Connector(f"http://{os.environ.get('GNS3_SERVER', 'localhost')}:3080")
GNS3_PROJECT_PATH = os.environ.get('GNS3_PROJECT_PATH', f"{os.path.expanduser('~')}/GNS3/projects")

# Map JSON node IDs to node IDs assigned by GNS3.
ID_MAP = {}
# Map node names to node IDs assigned by GNS3.
NAME_TO_ID = {}

# Resolve a template ID.
def get_template_id(template_name):
    template_name = template_name.lower()
    templates = GNS3_CONNECTOR.get_templates()
    for template in templates:
        if template['name'].lower().replace(" ", "") == template_name or template.get('platform', "").lower().replace(" ", "") == template_name:
            return template['template_id']

# Create a topology.
def create_topology(project, topology_json):
    # Create nodes and save their mappings.
    for node in topology_json['nodes']:
        # Set default coordinates when x and y are absent.
        name=node["label"]['text']
        x = node.get('x', 0)
        y = node.get('y', 0)
        properties = node.get('properties', {})
        filtered_properties = {}
        for k, v in properties.items():
            if k.startswith("slot") or k.startswith("mappings"):
                filtered_properties[k] = v
        template_id = get_template_id(node.get("type"))
        
        # Ask GNS3 to create the node and assign its node_id.
        gns3_node = gns3fy.Node(
            name=name,
            project_id=project.project_id,
            template_id=template_id,
            properties=filtered_properties,
            x=int(x),
            y=int(y),
            connector=GNS3_CONNECTOR
        )

        gns3_node.create()
        # Save the JSON-to-GNS3 node ID mapping.
        ID_MAP[node['id']] = gns3_node.node_id
        NAME_TO_ID[name] = gns3_node.node_id

    # Create links.
    for link in topology_json['links']:
        # Resolve the correct node IDs from the mapping.
        source_node_id = link.get('source_node_id')
        source_port_id = link.get('source_port_id')
        destination_node_id = link.get('destination_node_id')
        destination_port_id = link.get('destination_port_id')
        
        for node in topology_json['nodes']:
            if node['id'] == source_node_id:
                node0_id = ID_MAP[source_node_id]
                for port in node['ports']:
                    if port['id'] == source_port_id:
                        node0_label = port['name']
                        node0_adapter_number = int(port.get('slot_number', 0))
                        node0_port_number = int(port.get('port_number', 0))
            if node['id'] == destination_node_id:
                node1_id = ID_MAP[destination_node_id]
                for port in node['ports']:
                    if port['id'] == destination_port_id:
                        node1_label = port['name']
                        node1_adapter_number = int(port.get('slot_number', 0))
                        node1_port_number = int(port.get('port_number', 0))

        nodes = [
            {"node_id": node0_id, "adapter_number": node0_adapter_number, "port_number": node0_port_number, "label": {"text": node0_label}},
            {"node_id": node1_id, "adapter_number": node1_adapter_number, "port_number": node1_port_number, "label": {"text": node1_label}}
        ]  
        
        # Create the link.
        gns3_link = gns3fy.Link(
            project_id=project.project_id,
            nodes=nodes,
            connector=GNS3_CONNECTOR
        )
        gns3_link.create()

    # Create drawings.
    for note in topology_json.get('notes', {}):
        color = note.get('color', '#000000')
        text = note.get('text', '')

        width = len(text) * 10

        svg = (
            f'<svg width="{width}" height="25">'
            f'<text font-family="TypeWriter" font-size="10.0" font-weight="bold" fill="{color}" fill-opacity="1.0">{text}</text></svg>'
        )

        project.create_drawing(
            svg=svg,
            x = round(note.get("x", 0)),
            y = round(note.get("y", 0)),
            z = 0
        )
    
    for ellipse in topology_json.get('ellipses', {}):
        border_style = ellipse.get("border_style", 0)
        fill_color = ellipse.get("fill_color", "#000000")
        width = round(ellipse.get("width", 0))
        height = round(ellipse.get("height", 0))
        cx = width // 2
        cy = height // 2
        rx = round(width / 2)
        ry = round(height / 2)

        if border_style == 0:
            svg = (
                f'<svg width="{width}.0" height="{height}.0">'
                f'<ellipse cx="{cx}" rx="{rx}" cy="{cy}" ry="{ry}" '
                f'fill="{fill_color}" fill-opacity="1.0" stroke-width="2" stroke="#000000" /></svg>'
            )
        elif border_style == 1:
            svg = (
                f'<svg width="{width}.0" height="{height}.0">'
                f'<ellipse cx="{cx}" rx="{rx}" cy="{cy}" ry="{ry}" '
                f'fill="{fill_color}" fill-opacity="1.0" stroke-dasharray="5, 25" stroke-width="2" stroke="#000000" /></svg>'
            )
        elif border_style == 2:
            svg = (
                f'<svg width="{width}.0" height="{height}.0">'
                f'<ellipse cx="{cx}" rx="{rx}" cy="{cy}" ry="{ry}" '
                f'fill="{fill_color}" fill-opacity="1.0" stroke-dasharray="25, 25" stroke-width="2" stroke="#000000" /></svg>'
            )

        project.create_drawing(
            svg=svg,
            x = round(ellipse.get("x", 0)),
            y = round(ellipse.get("y", 0)),
            z = -1
        )

    for rectangle in topology_json.get('rectangles', {}):
        border_style = rectangle.get("border_style", 0)
        fill_color = rectangle.get("fill_color", "#000000")
        width = round(rectangle.get("width", 0))
        height = round(rectangle.get("height", 0))

        if border_style == 0:
            svg = (
            f'<svg width="{width}.0" height="{height}.0">'
            f'<rect x="0" y="0" width="{width}" height="{height}" '
            f'fill="{fill_color}" fill-opacity="1.0" stroke-width="2" stroke="#000000" /></svg>'
        )
        elif border_style == 1:
            svg = (
            f'<svg width="{width}.0" height="{height}.0">'
            f'<rect x="0" y="0" width="{width}" height="{height}" '
            f'fill="{fill_color}" fill-opacity="1.0" stroke-dasharray="5, 25" stroke-width="2" stroke="#000000" /></svg>'
        )
        elif border_style == 2:
            svg = (
            f'<svg width="{width}.0" height="{height}.0">'
            f'<rect x="0" y="0" width="{width}" height="{height}" '
            f'fill="{fill_color}" fill-opacity="1.0" stroke-dasharray="25, 25" stroke-width="2" stroke="#000000" /></svg>'
        )

        project.create_drawing(
            svg=svg,
            x = round(rectangle.get("x", 0)),
            y = round(rectangle.get("y", 0)),
            z = -1
        )


# Apply the initial state.
def set_initial_state(project, startup_configs, default_startup_configs):
    # Process every node.
    index = 1
    for name, node_id in NAME_TO_ID.items():
        try:
            if name in startup_configs:
                data = startup_configs[name]
            else:
                data = default_startup_configs[name]
            GNS3_CONNECTOR.http_call(
                method='POST',
                url=f"{GNS3_CONNECTOR.base_url}/projects/{project.project_id}/nodes/{node_id}/files/configs/i{index}_startup-config.cfg",
                data=data,
            )
            logger.debug(f"{name}'s Initial configs has been copied to i{index}_startup-config.cfg successfully...")
            index += 1
        except:
            continue

# Delete a project.
def delete_project(scenario):
    scenario = scenario
    project_path = f"{GNS3_PROJECT_PATH}/{scenario}"
    project = gns3fy.Project(name=scenario, path=project_path, connector=GNS3_CONNECTOR)

    try:
        project.get()
        project.delete()
        if project.project_id == None:
            logger.debug(f"Project {scenario} has been deleted successfully...")
    except:
        logger.debug(f"Project {scenario} does not exist...")

# Open a project.
def open_project(porject_name):
    scenario = porject_name
    project_path = f"{GNS3_PROJECT_PATH}/{porject_name}"
    project = gns3fy.Project(name=scenario, path=project_path, connector=GNS3_CONNECTOR)

    try:
        project.get()
        project.open()
    except Exception as e:
        logger.debug(f"Failed to open project {scenario}: {e}")
        pass

    if project.status != 'opened':
        logger.debug("Failed to open project.")
        return None

    logger.debug(f"Project {scenario} has been opened successfully...")
    
    return project

# Close a project and all of its nodes.
def close_project(porject_name):
    scenario = porject_name
    project_path = f"{GNS3_PROJECT_PATH}/{porject_name}"
    project = gns3fy.Project(name=scenario, path=project_path, connector=GNS3_CONNECTOR)

    try:
        project.get()
        project.stop_nodes()
        project.close()
    except:
        pass

    if project.status != 'opened':
        logger.debug(f"Project {scenario} has been closed successfully...")

def close_other_projects(scenario):
    projects = GNS3_CONNECTOR.projects_summary(is_print=False)
    for project in projects:
        if project[0] != scenario and project[4] != "closed":
            try:
                close_project(project[0])
            except:
                pass

# Create the network topology.
def create_new_project(scenario):
    scenario = scenario
    project_path = f"{GNS3_PROJECT_PATH}/{scenario}"
    
    project = gns3fy.Project(name=scenario, path=project_path, connector=GNS3_CONNECTOR)
    project.create()
    if project.drawings is None:
        project.drawings = []
    logger.debug(f"Project {scenario} has been created successfully...")

    if project.status != 'opened':
        logger.debug("Failed to create project.")
        exit(1)
    
    return project

# Command-line entry point.
if __name__ == "__main__":
    task = "task/011_OSPF Virtual Link.json"
    task_data = json.load(open(task, 'r'))

    scenario = task_data["scenario"]
    topology = task_data["topology"]

    delete_project(scenario)
    project = create_new_project(scenario)
    
    create_topology(project, topology)
