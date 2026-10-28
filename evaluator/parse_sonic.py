# evaluator/parse_sonic.py
# Parsers for SONiC (FRR 8.4.x) CLI output. Command formats verified against
# a live FRR 8.4.4 instance (vtysh text + json).
import re
import ipaddress
import json

COMMUNITY_NAMES = {"noAdvertise": "no-advertise", "noExport": "no-export",
                   "internet": "internet", "localAS": "local-as"}
ORIGIN_MAP = {"IGP": "i", "EGP": "e", "INCOMPLETE": "?"}


def _vtysh_inner(command):
    """'sudo vtysh -c "show ip route 1.2.3.0/24"' -> 'show ip route 1.2.3.0/24'."""
    m = re.search(r'-c\s+"(.+?)"\s*$', command.strip())
    return m.group(1).strip() if m else command.strip()


def _json_blob(output):
    s, e = output.find("{"), output.rfind("}")
    if s == -1 or e <= s:
        return {}
    try:
        return json.loads(output[s:e + 1])
    except json.JSONDecodeError:
        return {}


def _area(a):
    a = str(a)
    if a.count(".") == 3 and a.startswith("0.0.0."):
        return a.split(".")[-1]
    return a


IS_IP4 = re.compile(r'^\d{1,3}(?:\.\d{1,3}){3}$')


def _origin(p):
    o = p.get("origin", "")
    return ORIGIN_MAP.get(o, o)


def _communities(p):
    """FRR route json: community = {"string": "100:200 no-advertise", "list": [...]};
    normalized to a list of string communities (noAdvertise -> no-advertise)."""
    c = p.get("community")
    if not c:
        return []
    if isinstance(c, dict):
        if c.get("list"):
            return [COMMUNITY_NAMES.get(x, x) for x in c["list"]]
        if c.get("string"):
            return [COMMUNITY_NAMES.get(x, x) for x in c["string"].split()]
    return str(c).split()


def _as_path(p):
    a = p.get("aspath")
    if isinstance(a, dict) and a.get("string"):
        return a["string"]
    return p.get("path", "") or ""


def _bestpath(p):
    b = p.get("bestpath")
    if isinstance(b, dict):
        return bool(b.get("overall"))
    return bool(b)


def _nexthop(p):
    nhs = p.get("nexthops") or [{}]
    nh = nhs[0].get("ip", "")
    return "" if nh in ("(unspec)", "0.0.0.0") else nh


# ---------------------------------------------------------------- route table

def parse_frr_route_table(text):
    """`show ip route` -> {protocol, network, prefix_length, nexthop_ip}."""
    routes = []
    for raw in text.splitlines():
        m = re.match(r'^([A-Z])[>*a-z\s]*\s*(\d+\.\d+\.\d+\.\d+)/(\d+)\b', raw.strip())
        if not m:
            continue
        rec = {"protocol": m.group(1), "network": m.group(2),
               "prefix_length": m.group(3), "nexthop_ip": ""}
        m2 = re.search(r'\bvia (\d+\.\d+\.\d+\.\d+)', raw)
        if m2:
            rec["nexthop_ip"] = m2.group(1)
        routes.append(rec)
    return routes


def parse_frr_ip_route_detail(text):
    """Parse `show ip route <prefix|addr>` (FRR): one record per nexthop.
    FRR prints: Routing entry for X / Known via "bgp", distance 20, metric 0[, best]
    / * <nh>, via <if>[, weight N].  BGP-learned routes carry no 'from' line;
    the nexthop IS the learned-from peer for BGP."""
    records, entry, got_nh = [], None, False

    def close():
        if entry is not None and not got_nh:
            records.append(dict(entry, nexthop_ip="", nexthop_if="",
                                learned_from=""))

    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r'^Routing entry for (\S+)', line)
        if m:
            close()
            entry, got_nh = {"network": m.group(1), "protocol": "",
                             "distance": "", "metric": ""}, False
            continue
        if entry is None:
            continue
        m = re.match(r'^Known via "([^"]+)", distance (\d+)(?:, metric (\d+))?', line)
        if m:
            entry["protocol"] = m.group(1).lower()
            entry["distance"] = m.group(2)
            entry["metric"] = m.group(3) or ""
            continue
        m = re.match(r'^[>*qrbto\s]*(\d+\.\d+\.\d+\.\d+),\s*via\s+(\S+?)(?:,|$)', line)
        if m:
            rec = dict(entry, nexthop_ip=m.group(1), nexthop_if=m.group(2))
            rec["learned_from"] = m.group(1) if entry["protocol"] == "bgp" else ""
            records.append(rec)
            got_nh = True
            continue
        m = re.match(r'^\*?\s*directly connected,\s*(\S+?)(?:,|$)', line)
        if m:
            records.append(dict(entry, nexthop_ip="", nexthop_if=m.group(1),
                                learned_from=""))
            got_nh = True
    close()
    return records


def parse_frr_running_routes(text):
    """Parse 'ip route <prefix> <nexthop> [distance]' lines from running-config."""
    out = []
    for raw in text.splitlines():
        p = raw.strip().split()
        if len(p) < 3 or p[0] != "ip" or p[1] != "route":
            continue
        try:
            net = str(ipaddress.IPv4Network(p[2], strict=False))
        except ValueError:
            continue
        rec = {"destination": net, "nexthop_ip": None, "distance": None}
        for tok in p[3:]:
            if rec["nexthop_ip"] is None and not tok.isdigit():
                rec["nexthop_ip"] = tok            # IP, or Null0/iface/blackhole
            elif tok.isdigit() and rec["distance"] is None:
                rec["distance"] = tok
        out.append(rec)
    return out


# ---------------------------------------------------------------------- OSPF

def parse_ospf_interface_json(output):        # "show ip ospf interface json"
    data = _json_blob(output)
    ifs = data.get("interfaces", data)         # wrapped in newer FRR
    rows = []
    for name, i in ifs.items():
        if not isinstance(i, dict) or i.get("ospfEnabled") is False or "area" not in i:
            continue
        rows.append({"interface": name, "area": _area(i["area"]),
                     "cost": i.get("cost"), "state": i.get("state"),
                     "ip_address": i.get("ipAddress")})
    return rows


def parse_ospf_neighbor_json(output):         # "show ip ospf neighbor json"
    data = _json_blob(output)
    nbrs = data.get("neighbors", data)
    rows = []
    for nid, entries in nbrs.items():
        for n in ([entries] if isinstance(entries, dict) else entries):
            rows.append({"neighbor_id": nid,
                         "state": str(n.get("nbrState") or n.get("state") or "").split("/")[0],
                         "ip_address": n.get("address") or n.get("ifaceAddress"),
                         "interface": str(n.get("ifaceName", "")).split(":")[0],
                         "priority": n.get("priority")})
    return rows


# ----------------------------------------------------------------------- BGP

def _neighbor_fields(info):
    """Merge top-level neighbor json with addressFamilyInfo sub-dicts."""
    merged = {}
    for afi, afinfo in info.get("addressFamilyInfo", {}).items():
        if isinstance(afinfo, dict):
            for k, v in afinfo.items():
                merged.setdefault(k, v)
    for k, v in info.items():
        merged[k] = v
    return merged


def parse_frr_bgp_neighbors_json(output):     # "show ip bgp neighbors [ip] json"
    data = _json_blob(output)
    rows = []
    for ip, info in data.items():
        if not isinstance(info, dict) or "bgpState" not in info:
            continue
        d = _neighbor_fields(info)
        send = d.get("commAttriSentToNbr", "")
        rows.append({
            "neighbor": ip,
            "remote_as": str(d.get("remoteAs", "")),
            "bgp_state": d.get("bgpState", ""),
            "localhost_ip": d.get("hostLocal", ""),
            "remote_ip": d.get("hostForeign", ip),
            "peer_group": d.get("peerGroup", ""),
            "send_community": send not in ("", "none"),
            "inbound_soft_reconfiguration": bool(d.get("inboundSoftConfigPermit")),
            "inbound_routemap": d.get("routeMapForIncomingAdvertisements", ""),
            "outbound_routemap": d.get("routeMapForOutgoingAdvertisements", ""),
            "remote_router_id": d.get("remoteRouterId", ""),
            "update_source": d.get("updateSource", ""),
            "maximum_prefix": str(d.get("prefixAllowedMax", "")),
            "incoming_filter": d.get("incomingUpdatePrefixFilterList", ""),
        })
    return rows


def parse_frr_bgp_summary_json(output):
    data = _json_blob(output)
    for afi in data.values():
        if isinstance(afi, dict) and "as" in afi:
            return [{"protocol": "bgp", "as_number": str(afi["as"]),
                     "local_as": str(afi["as"]), "router_id": afi.get("routerId", "")}]
    return []


def parse_frr_bgp_prefix_json(output):
    """`show ip bgp A.B.C.D/len json` -> per-path dicts; [] when prefix absent.
    FRR json: {"prefix": ..., "paths": [{aspath{string}, origin, metric, locPrf,
    weight, valid, bestpath{overall}, nexthops[{ip}], community{...}}]}."""
    data = _json_blob(output)
    paths = data.get("paths")
    if not paths:
        return []
    pfx = str(data.get("prefix", ""))
    rows = []
    for p in paths:
        rows.append({
            "network": pfx,
            "next_hop": _nexthop(p),
            "as_path": _as_path(p),
            "origin": _origin(p),
            "metric": str(p.get("metric", "")),
            "local_pref": str(p.get("locPrf", "")),
            "weight": str(p.get("weight", "")),
            "communities": _communities(p),
            "status": "s" if p.get("suppressed") else ("*" if p.get("valid") else ""),
        })
    return rows


def parse_frr_bgp_routes_json(output):        # "show ip bgp json"
    data = _json_blob(output)
    routes = data.get("routes")
    if not isinstance(routes, dict):
        return []
    rows = []
    for prefix, plist in routes.items():
        for p in ([plist] if isinstance(plist, dict) else plist):
            rows.append({
                "network": p.get("network", prefix),
                "next_hop": _nexthop(p),
                "as_path": _as_path(p),
                "origin": _origin(p),
                "metric": str(p.get("metric", "")),
                "local_pref": str(p.get("locPrf", "")),
                "weight": str(p.get("weight", "")),
                "communities": _communities(p),
                "status": "s" if p.get("suppressed") else ("*" if p.get("valid") else ""),
            })
    return rows


def parse_frr_bgp_table_text(text):
    """`show ip bgp` (text). Column layout from the header line:
       Network  Next Hop  Metric  LocPrf  Weight  Path[ origin].
       Status flags (s,d,*,>,...) sit in the 3 chars before Network.
       LocPrf is blank for locally originated routes -> column slicing."""
    routes = []
    cols = None
    for raw in text.splitlines():
        if cols is None:
            m = re.search(r'Network\s+Next Hop\s+Metric\s+LocPrf\s+Weight\s+Path\s*$', raw)
            if m:
                base = m.start()
                cols = {w: raw.index(w, base) for w in
                        ("Network", "Next Hop", "Metric", "LocPrf", "Weight", "Path")}
            continue
        m = re.match(r'^(\S{1,4})\s(\S+/\d+.*)$', raw)
        if not m:
            continue
        flags, _rest = m.groups()

        def cell(w):
            s = cols[w]
            later = sorted(v for v in cols.values() if v > s)
            e = later[0] if later else len(raw)
            return raw[s:e].strip() if s < len(raw) else ""

        net = cell("Network")
        if not re.match(r'^\d+\.\d+\.\d+\.\d+/\d+$', net):
            continue
        tokens = cell("Path").split()
        origin = tokens[-1] if tokens and re.fullmatch(r'[ie?]', tokens[-1]) else ""
        aspath = " ".join(tokens[:-1]) if origin else " ".join(tokens)
        routes.append({
            "network": net,
            "status": "s" if "s" in flags else ("*" if "*" in flags else ""),
            "path_selection": ">" if ">" in flags else "",
            "next_hop": cell("Next Hop"),
            "metric": cell("Metric") or "",
            "local_pref": cell("LocPrf") or "",
            "weight": cell("Weight") or "",
            "as_path": aspath,
            "origin": origin,
        })
    return routes


# ------------------------------------------------- prefix-list / community

def parse_frr_prefix_list(text):
    """`show ip prefix-list` (FRR):
       BGP: ip prefix-list NAME: N entries
          seq 5 permit 1.1.1.1/32 [le|ge N] [le|ge N]"""
    rows, name = [], None
    for raw in text.splitlines():
        m = re.search(r'ip prefix-list (\S+):', raw)
        if m:
            name = m.group(1)
            continue
        m = re.match(r'^\s*seq (\d+) (permit|deny) (\d+\.\d+\.\d+\.\d+)/(\d+)'
                     r'(?: (le|ge) (\d+))?(?: (le|ge) (\d+))?\s*$', raw)
        if m and name:
            rec = {"name": name, "seq": int(m.group(1)), "action": m.group(2),
                   "network": m.group(3), "netmask": m.group(4)}
            if m.group(5):
                rec[m.group(5)] = m.group(6)
            if m.group(7):
                rec[m.group(7)] = m.group(8)
            rows.append(rec)
    return rows


def parse_frr_community_list(text):
    """`show bgp community-list [N]` (FRR):
       Community standard list 5
           permit 100:100"""
    rows, typ, name = [], None, None
    for raw in text.splitlines():
        m = re.match(r'^Community (standard|expanded) list (\S+)', raw)
        if m:
            typ, name = m.group(1), m.group(2)
            continue
        m = re.match(r'^\s*(permit|deny)\s+(.+?)\s*$', raw)
        if m and typ:
            rows.append({"name": name, "community_type": typ,
                         "action": m.group(1), "community": m.group(2)})
    return rows


def parse_frr_route_map(text):
    """`show route-map` (FRR): per-daemon headers (ZEBRA:/BGP:...), then
       route-map: NAME ... / permit, sequence N / Match clauses: / Set clauses:"""
    maps, name, section = [], None, None
    for raw in text.splitlines():
        line = raw.rstrip()
        if re.fullmatch(r'[A-Za-z]+:', line.strip()):
            continue
        if "not found" in line:
            continue
        m = re.match(r'^route-map: (\S+)', line)
        if m:
            name = m.group(1)
            section = None
            maps.append({"name": name, "action": "", "seq": None,
                         "match_clauses": [], "set_clauses": []})
            continue
        m = re.match(r'^\s*(permit|deny), sequence (\d+)', line)
        if m and maps:
            maps[-1]["action"], maps[-1]["seq"] = m.group(1), int(m.group(2))
            continue
        if re.match(r'^\s*Match clauses:', line):
            section = "match_clauses"
            continue
        if re.match(r'^\s*Set clauses:', line):
            section = "set_clauses"
            continue
        if re.match(r'^\s*(Call clause:|Action:)', line):
            section = None
            continue
        if maps and section and line.strip():
            maps[-1][section].append(line.strip())
    for r in maps:
        names = []
        for c in r["match_clauses"]:
            m = re.match(r'^(?:Match )?ip address prefix-list (\S+)$', c)
            if m:
                names.append(m.group(1))
        r["match_ip_filter_names"] = names
    return maps


# -------------------------------------------------------------------- EIGRP

def parse_frr_eigrp_neighbors(text):
    """`show ip eigrp neighbors` (FRR): H Address Interface Hold Uptime SRTT RTO Q Seq"""
    rows = []
    for raw in text.splitlines():
        t = raw.split()
        if len(t) >= 7 and re.fullmatch(r'\d+', t[0]) and IS_IP4.fullmatch(t[1]):
            rows.append({"ip_address": t[1], "interface": t[2]})
    return rows


def parse_frr_eigrp_topology(text):
    """`show ip eigrp topology` (FRR): 'P  10.0.0.0/24, 1 successors, FD is N'."""
    rows = []
    for raw in text.splitlines():
        m = re.match(r'^\s*[PAUQRrs]{0,3}\s+(\d+\.\d+\.\d+\.\d+)/(\d+)', raw)
        if m:
            rows.append({"network": m.group(1), "prefix_length": m.group(2)})
    return rows


# --------------------------------------------------------------------- ping

def parse_ping(output):
    m = re.search(r'(\d+(?:\.\d+)?)% packet loss', output)
    pct = int(round(100 - float(m.group(1)))) if m else 0
    return [{"success_pct": str(pct)}]


# ----------------------------------------------------------------- dispatch

def parse_sonic_cli_output(command, output):
    inner = _vtysh_inner(command)
    if inner.startswith("ping "):
        return parse_ping(output)
    if inner == "show ip ospf interface json":
        return parse_ospf_interface_json(output)
    if inner == "show ip ospf neighbor json":
        return parse_ospf_neighbor_json(output)
    if inner == "show ip eigrp neighbors":
        return parse_frr_eigrp_neighbors(output)
    if inner == "show ip eigrp topology":
        return parse_frr_eigrp_topology(output)
    if inner == "show ip bgp summary json":
        return parse_frr_bgp_summary_json(output)
    if re.fullmatch(r'show ip bgp \d+\.\d+\.\d+\.\d+/\d+ json', inner):
        return parse_frr_bgp_prefix_json(output)
    if inner.startswith("show ip bgp neighbors") and inner.endswith("json"):
        return parse_frr_bgp_neighbors_json(output)
    if inner == "show ip bgp json":
        return parse_frr_bgp_routes_json(output)
    if inner == "show ip bgp":
        return parse_frr_bgp_table_text(output)
    if inner == "show ip prefix-list":
        return parse_frr_prefix_list(output)
    if re.fullmatch(r'show bgp community-list(?: \S+)?', inner):
        return parse_frr_community_list(output)
    if inner == "show route-map":
        return parse_frr_route_map(output)
    if re.fullmatch(r'show ip route \S+', inner):
        return parse_frr_ip_route_detail(output)
    if inner == "show ip route":
        return parse_frr_route_table(output)
    if inner == "show running-config" or inner.startswith("show running-config | include ^ip route"):
        return parse_frr_running_routes(output)
    rows = []
    for l in output.splitlines():
        l = l.strip()
        if not l:
            continue
        rows.append({"line": l})
        # IOS-style twin for aggregate-address lines (net <mask> instead of /len)
        m = re.match(r'^aggregate-address (\d+\.\d+\.\d+\.\d+)/(\d+)(.*)$', l)
        if m:
            inv = 0
            for o in range(4):
                inv = (inv << 8) | (255 if o < int(m.group(2)) else 0)
            mask = ".".join(str((inv >> s) & 255) for s in (24, 16, 8, 0))
            rows.append({"line": "aggregate-address %s %s%s" % (m.group(1), mask, m.group(3))})
    return rows
