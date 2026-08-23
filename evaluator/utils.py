import re
import ipaddress
from collections import Counter
from ciscoconfparse2.ciscoconfparse2 import Diff
from logs.mylogger import logger

def generate_configs_diff(old_configs_dict, new_configs_dict):
    diff_commands = {}

    for key, value in old_configs_dict.items():
                
        # Parse the original configuration
        old_config = value
        new_config = new_configs_dict.get(key, None)
        if new_config is None:
            logger.debug(f"Node {key} not found in new configurations.")
            continue
        diff = Diff(old_config=old_config, new_config=new_config)
        results = diff.get_diff()
        diff_commands.update({key: results})
    
    return diff_commands

def _classful_default_mask(ip_str):
    first = int(ip_str.split('.')[0])
    if 1 <= first <= 126:
        return "255.0.0.0"
    if 128 <= first <= 191:
        return "255.255.0.0"
    if 192 <= first <= 223:
        return "255.255.255.0"
    return None

def normalize_config_line(line):
    # For `network X mask Y`, IOS omits `mask Y` from show running-config when
    # Y is X's classful default mask. Normalize the template form accordingly.
    m = re.match(r'^(\s*)network\s+(\d+\.\d+\.\d+\.\d+)\s+mask\s+(\d+\.\d+\.\d+\.\d+)\s*$', line)
    if m:
        indent, ip, mask = m.group(1), m.group(2), m.group(3)
        if mask == _classful_default_mask(ip):
            return f"{indent}network {ip}"
    return line

def compute_precision_and_recall(pred_configs, groundtruth_configs):
    results = {}

    micro_true_positives = 0
    micro_total_pred = 0
    micro_total_ground_truth = 0

    false_positives_all = {}
    false_negatives_all = {}

    for node in pred_configs:
        if node not in groundtruth_configs:
            continue

        pred_counter = Counter(normalize_config_line(l) for l in pred_configs[node])
        ground_truth_counter = Counter(normalize_config_line(l) for l in groundtruth_configs[node])

        # Matching entries (intersection).
        common_counter = pred_counter & ground_truth_counter
        true_positives = sum(common_counter.values())

        total_pred = sum(pred_counter.values())
        total_ground_truth = sum(ground_truth_counter.values())

        # Record mismatches (difference).
        false_positives = pred_counter - ground_truth_counter
        false_negatives = ground_truth_counter - pred_counter

        if false_positives:
            false_positives_all[node] = dict(false_positives)
        if false_negatives:
            false_negatives_all[node] = dict(false_negatives)

        micro_true_positives += true_positives
        micro_total_pred += total_pred
        micro_total_ground_truth += total_ground_truth

    results = {
        "true_positives": micro_true_positives,
        "pred": micro_total_pred,
        "ground_truth": micro_total_ground_truth,
    }

    return results

def resolve_references(obj, shared_data):
    if isinstance(obj, dict):
        return {k: resolve_references(v, shared_data) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [resolve_references(item, shared_data) for item in obj]
    elif isinstance(obj, str):
        # Find every $variable_name$ reference.
        def replace_var(match):
            var_name = match.group(1)  # Name between the dollar signs.
            return str(shared_data.get(var_name, match.group(0)))  # Preserve unknown names.
        # Replace every variable reference in the string.
        return re.sub(r'\$([^$]+)\$', replace_var, obj)
    else:
        return obj
    
def compare_results(expected_result, actual_result, mode):
    if expected_result is None:
        if actual_result is None or actual_result == []:
            return True
        else:
            return False

    if is_operator_assertion(expected_result):
        return compare_operator(expected_result, actual_result)
    
    if type(expected_result) == dict:
        if mode == "strict":
            for item in actual_result:
                if not is_subdict(expected_result, item):
                    return False
            return True
        else:
            for item in actual_result:
                if is_subdict(expected_result, item):
                    return True
            return False

    if type(expected_result) == list:
        # Default mode: expected results must be a subset of actual results.
        if mode == "default":
            for item1 in expected_result:
                is_contains = False
                for item2 in actual_result:
                    if is_subdict(item1, item2):
                        is_contains = True
                        break
                if not is_contains:
                    return False
            return True
        
        # Contains mode: actual results are a non-empty subset of expected results.
        elif mode == "contains":
            if len(actual_result) == 0:
                return False
            for item1 in actual_result:
                is_contains = False
                for item2 in expected_result:
                    if is_subdict(item1, item2):
                        is_contains = True
                        break
                if not is_contains:
                    return False
            return True
        
        # Strict mode: count and order must match, including for empty lists.
        elif mode == "strict":
            if len(expected_result) != len(actual_result):
                return False
            for item1, item2 in zip(expected_result, actual_result):
                if not is_subdict(item1, item2, strict=True):
                    return False
            return True
        
def is_subdict(d1, d2, strict=False):
    """
    Return whether d1 is a subset of d2.
    Nested dictionaries are supported; lists use subset semantics by default.
    compare_funcs can provide key-specific comparisons.
    """
    compare_funcs = {
        "networks": compare_networks,
        "network": compare_network_string,
        "interface": compare_interfaces,
        "passive_interfaces": compare_interfaces,
        "metric_added": compare_value,
        # "distance": compare_value,
        # "networks_detailed": compare_list_of_dict,
        # "entries": compare_list_of_dict,
        # "incoming_metric": compare_list_of_dict,
        # "outcoming_metric": compare_list_of_dict,
        # "distance_overrides": compare_list_of_dict,
        # "virtual_links": compare_list_of_dict,
        # "redistribute": compare_list_of_dict,
        "cost": compare_value,
        # "areas": compare_list_of_dict,
        # "ranges": compare_list_of_dict,
        # "lsas": compare_list_of_dict,
        # "aggregate_generation": compare_list_of_dict,
        "set_clauses": compare_set_clauses
    }

    for key, val1 in d1.items():
        full_path = f"{key}"

        if key not in d2:
            logger.debug(f"Key '{full_path}' missing in d2")
            return False

        val2 = d2[key]

        if val1 is None:
            if val2 is None or val2 == []:
                continue
            else:
                logger.debug(f"Key '{full_path}' in d1 is None, but in d2 it is {val2}")
                return False

        if is_operator_assertion(val1):
            if not compare_operator(val1, val2):
                logger.debug(f"Key '{full_path}' failed operator compare: {val1} vs {val2}")
                return False

        elif key in compare_funcs:
            if not compare_funcs[key](val1, val2):
                logger.debug(f"Key '{full_path}' failed custom compare: {val1} vs {val2}")
                return False
            
        # Dictionary value.
        elif isinstance(val1, dict) and isinstance(val2, dict):
            if not is_subdict(val1, val2, full_path):
                return False

        # List value whose elements are hashable.
        elif isinstance(val1, list) and isinstance(val2, list):
            if strict:
                return val1 == val2
            else:
                if not set(val1).issubset(set(val2)):
                    logger.debug(f"List mismatch at '{full_path}': {val1} not subset of {val2}")
                    return False

        # Other value types are compared directly.
        elif val1 != val2:
            logger.debug(f"Value mismatch at '{full_path}': {val1} != {val2}")
            return False

    return True

def is_operator_assertion(value):
    return isinstance(value, dict) and "op" in value

def coerce_comparable(value):
    if isinstance(value, str):
        stripped = value.strip()
        try:
            if re.fullmatch(r"[-+]?\d+", stripped):
                return int(stripped)
            if re.fullmatch(r"[-+]?\d+\.\d+", stripped):
                return float(stripped)
        except ValueError:
            return value
    return value

def membership_test(needle, haystack):
    # `x in <str>` requires x to be a string, while coerce_comparable converts
    # numeric strings such as "1424" to int. Use string substring matching
    # whenever the container is a string; preserve normal semantics otherwise.
    if haystack is None:
        return False
    if isinstance(haystack, str):
        return str(needle) in haystack
    try:
        return needle in haystack
    except TypeError:
        return False

def compare_operator(expected, actual):
    op = expected.get("op")
    expected_value = expected.get("value")

    if op in ("==", "eq"):
        return coerce_comparable(actual) == coerce_comparable(expected_value)
    if op in ("!=", "ne"):
        return coerce_comparable(actual) != coerce_comparable(expected_value)

    actual_value = coerce_comparable(actual)
    expected_value = coerce_comparable(expected_value)

    if op in (">", "gt"):
        return actual_value > expected_value
    if op in (">=", "gte"):
        return actual_value >= expected_value
    if op in ("<", "lt"):
        return actual_value < expected_value
    if op in ("<=", "lte"):
        return actual_value <= expected_value

    if op == "in":
        return membership_test(actual, expected_value)
    if op == "not_in":
        return not membership_test(actual, expected_value)
    if op == "contains":
        return membership_test(expected_value, actual)
    if op == "not_contains":
        return not membership_test(expected_value, actual)
    if op == "contains_all":
        return compare_contains(expected_value, actual, require_all=True)
    if op == "contains_any":
        return compare_contains(expected_value, actual, require_all=False)
    if op == "all_match":
        return compare_all_match(expected_value, actual)
    if op == "count_match":
        return compare_count_match(expected, actual)
    if op == "regex":
        return re.search(str(expected_value), str(actual)) is not None
    if op == "empty":
        return actual is None or actual == []
    if op == "not_empty":
        return actual is not None and actual != []

    logger.debug(f"Unsupported compare operator: {op}")
    return False

def compare_contains(expected_items, actual_items, require_all=True):
    if not isinstance(expected_items, list):
        expected_items = [expected_items]

    def contains(expected_item):
        if isinstance(expected_item, dict) and isinstance(actual_items, list):
            return any(
                isinstance(actual_item, dict) and is_subdict(expected_item, actual_item)
                for actual_item in actual_items
            )
        return expected_item in actual_items

    if require_all:
        return all(contains(item) for item in expected_items)
    return any(contains(item) for item in expected_items)

def compare_all_match(expected_item, actual_items):
    if actual_items is None or actual_items == []:
        return False

    expected_items = expected_item if isinstance(expected_item, list) else [expected_item]

    def matches(actual_item):
        for item in expected_items:
            if isinstance(item, dict) and isinstance(actual_item, dict):
                if is_subdict(item, actual_item):
                    return True
            elif coerce_comparable(actual_item) == coerce_comparable(item):
                return True
        return False

    if isinstance(actual_items, list):
        return all(matches(item) for item in actual_items)
    return matches(actual_items)

def compare_count_match(expected, actual_items):
    if not isinstance(actual_items, list):
        return False

    match_item = expected.get("match", {})
    distinct_key = expected.get("distinct")
    count_expected = expected.get("count", {"op": ">=", "value": 1})

    matched_items = [
        item for item in actual_items
        if isinstance(item, dict) and is_subdict(match_item, item)
    ]

    if distinct_key:
        count_actual = len({
            item.get(distinct_key)
            for item in matched_items
            if item.get(distinct_key) not in (None, "")
        })
    else:
        count_actual = len(matched_items)

    if is_operator_assertion(count_expected):
        return compare_operator(count_expected, count_actual)
    return coerce_comparable(count_actual) == coerce_comparable(count_expected)

def compare_network_string(a, b):
    # For singular `network` fields (BGP/show ip bgp), allow equivalent forms
    # with or without /N. Match IOS semantics: a bare address uses its classful
    # default (A=/8, B=/16, C=/24), since IOS omits only default masks. Thus
    # `192.168.1.0` equals `192.168.1.0/24`, while `1.6.189.0` means /8. A
    # template requiring the latter as /24 must write `1.6.189.0/24` explicitly.
    def parse_classful(s):
        s = s.strip()
        if "/" in s:
            return ipaddress.IPv4Network(s, strict=False)
        first = int(s.split('.')[0])
        if 1 <= first <= 126: prefix = 8
        elif 128 <= first <= 191: prefix = 16
        elif 192 <= first <= 223: prefix = 24
        else: return None
        return ipaddress.IPv4Network(f"{s}/{prefix}", strict=False)

    if not isinstance(a, str) or not isinstance(b, str):
        return a == b
    try:
        return parse_classful(a) == parse_classful(b)
    except (ValueError, ipaddress.AddressValueError):
        return a == b

def compare_networks(a, b):
    def get_standard_network(ip_str):
        first_octet = int(ip_str.split('.')[0])
        if 1 <= first_octet <= 126:
            return ipaddress.IPv4Network(f"{ip_str}/8")
        elif 128 <= first_octet <= 191:
            return ipaddress.IPv4Network(f"{ip_str}/16")
        elif 192 <= first_octet <= 223:
            return ipaddress.IPv4Network(f"{ip_str}/24")
        else:
            raise ValueError("Not a classful A/B/C IP address")
    
    if '0.0.0.0' in b:
        return True
    b = [get_standard_network(i) for i in b]

    a_networks = [ipaddress.ip_network(s) for s in a]
    b_networks = [ipaddress.ip_network(s) for s in b]

    for a_net in a_networks:
        if not any(a_net.subnet_of(b_net) for b_net in b_networks):
            logger.debug(f"{a_net} is not contained in any of B")
            return False
    return True
    
def compare_interfaces(a, b):
    def normalize_interface_name(interface_name):
        """Normalize interface names by expanding abbreviations."""
        if not isinstance(interface_name, str):
            return interface_name
        
        # Map interface abbreviations to full names.
        interface_mappings = {
            r'^lo(\d+)$': r'loopback\1',
            r'^loopback(\d+)$': r'loopback\1',
            r'^eth(\d+)$': r'ethernet\1',
            r'^ethernet(\d+)$': r'ethernet\1',
            r'^et(\d+/\d+)$': r'ethernet\1',
            r'^ethernet(\d+/\d+)$': r'ethernet\1',
            r'^fa(\d+/\d+)$': r'fastethernet\1',
            r'^fastethernet(\d+/\d+)$': r'fastethernet\1',
            r'^gi(\d+/\d+)$': r'gigabitethernet\1',
            r'^gigabitethernet(\d+/\d+)$': r'gigabitethernet\1',
            r'^se(\d+/\d+)$': r'serial\1',
            r'^serial(\d+/\d+)$': r'serial\1',
            r'^tu(\d+)$': r'tunnel\1',
            r'^tunnel(\d+)$': r'tunnel\1',
            r'^vl(\d+)$': r'vlan\1',
            r'^vlan(\d+)$': r'vlan\1',
        }
        
        normalized_name = interface_name.lower()
        for pattern, replacement in interface_mappings.items():
            normalized_name = re.sub(pattern, replacement, normalized_name)
        
        return normalized_name
    
    # Normalize interface names.
    if isinstance(a, str):
        a = normalize_interface_name(a)
    elif isinstance(a, list):
        a = [normalize_interface_name(item) for item in a]
    
    if isinstance(b, str):
        b = normalize_interface_name(b)
    elif isinstance(b, list):
        b = [normalize_interface_name(item) for item in b]
    
    if isinstance(b, str):
        if b == "ALL" or b == "default":
            return True
        else:
            return a == b
    elif isinstance(b, list):
        if "default" in b or "ALL" in b:
            return True
        else:
            if isinstance(a, str):
                if a not in b:
                    logger.debug(f"{a} is not in B")
                    return False
            else:
                for a_interface in a:
                    if a_interface not in b:
                        logger.debug(f"{a_interface} is not in B")
                        return False
                return True
            

def compare_value(a, b):
    if isinstance(a, str):
        a = int(a)
    if isinstance(b, str):
        b = int(b)
    return a <= b

def compare_set_clauses(a, b):
    for a_item in a:
        is_substr = False
        for b_item in b:
            if a_item in b_item:
                is_substr = True
                break
        if is_substr == False:
            return False
    return True
