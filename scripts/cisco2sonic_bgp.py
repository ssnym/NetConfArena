#!/usr/bin/env python3
"""Cisco->SONiC (FRR 8.4.x) converter for NetConfArena BGP tasks, v3.
Rule tables validated against a live FRR 8.4.4 instance; anything without a
rule is reported, never guessed.
Usage: python3 scripts/cisco2sonic_bgp.py --src task --dst sonic-gen [--only REGEX]
"""
import argparse, collections, copy, glob, json, os, re, shlex, shutil

PHRE = re.compile(r'\{\{\s*([^}\s]+)\s*\}\}')
DEFAULT_LO0 = "10.1.0.1/32"
DROP = {"no auto-summary", "no synchronization", "synchronization",
        "bgp log-neighbor-changes", "log-adjacency-changes",
        "ip bgp-community new-format", "version 2", "!"}
IFACE_DROP = {"no cdp enable", "no mips", "no ip mroute", "no ip redirect",
              "no ip unreachables"}
BGP_PASS = (r"bgp confederation (identifier|peers) .+", r"bgp cluster-id \S+",
            r"bgp always-compare-med", r"bgp router-id \S+",
            r"bgp bestpath (as-path ignore|compare-routerid)")
# BGP lines with no FRR equivalent; dropped with a note (task stays convertible)
BGP_DROP = (r"bgp dmzlink-bw", r"bgp maxas-limit \d+",
            r"default (local-preference|origin|metric) \S+",
            r"no bgp default route-target filter")
NBR_DROP = (r"maximum-as-limit \d+",)
NBR_GLOBAL = r"(remote-as \S+|ebgp-multihop( \d+)?|update-source \S+|peer-group( \S+)?)"
NBR_AF = (r"next-hop-self", r"route-reflector-client", r"soft-reconfiguration inbound",
          r"allowas-in( \d+)?", r"send-community( \S+)?", r"route-map \S+ (in|out)",
          r"prefix-list \S+ (in|out)", r"unsuppress-map \S+",
          r"advertise-map \S+ (exist|non-exist)-map \S+", r"default-originate",
          r"remove-private-as( all)?", r"maximum-prefix( \d+)?(?: \d+)?")
RM_KID = (r"match (ip address( prefix-list)? \S+|as-path \S+|community \S+)|"
          r"set (weight|metric|local-preference|origin|as-path prepend|community|comm-list|ip next-hop) .+")
OSPF_DROP = (r"auto-cost reference-bandwidth \d+",)
# expected_result keys each parser can produce (evaluator/parse_sonic.py)
PARSER_KEYS = {
    "nbr": {"neighbor", "remote_as", "bgp_state", "localhost_ip", "remote_ip",
            "peer_group", "send_community", "inbound_soft_reconfiguration",
            "inbound_routemap", "outbound_routemap", "remote_router_id",
            "update_source", "maximum_prefix", "incoming_filter"},
    "bgp_table": {"network", "status", "path_selection", "next_hop", "metric",
                  "local_pref", "weight", "as_path", "origin"},
    "bgp_summary": {"local_as", "as_number", "protocol", "router_id"},
    "bgp_prefix": {"network", "next_hop", "as_path", "origin", "metric",
                   "local_pref", "weight", "communities", "status"},
    "rt": {"protocol", "network", "prefix_length", "nexthop_ip"},
    "rt_detail": {"network", "protocol", "nexthop_ip", "nexthop_if", "distance",
                  "metric", "learned_from"},
    "pfxlist": {"name", "seq", "action", "network", "netmask", "le", "ge"},
    "cmlist": {"name", "community_type", "action", "community"},
    "rmap": {"name", "action", "seq", "match_clauses", "set_clauses",
             "match_ip_filter_names"},
    "eigrp_nbr": {"ip_address", "interface"},
    "eigrp_topo": {"network", "prefix_length"},
    "iproute_cfg": {"destination", "nexthop_ip", "distance"},
    "ospf_nbr": {"neighbor_id", "state", "ip_address", "interface", "priority"},
    "ping": {"success_pct"},
    "ospf_if": {"interface", "area"},
    "line": {"line"},
}
META_KEYS = {"op", "value", "count", "match"}
GOAL_FLAGS = re.compile(r"dual-as|synchroniz|\bIOS\b|Cisco|wildcard|\bmask\b|show ", re.I)
IS_IP = re.compile(r'^\d{1,3}(?:\.\d{1,3}){3}$')


def load_yaml(path):
    """Minimal flat-list YAML reader (params.yaml is flat: scalars + string lists)."""
    if not os.path.exists(path):
        return {}
    out, cur = {}, None
    for ln in open(path):
        s = ln.split("#", 1)[0].rstrip()
        if not s.strip():
            continue
        if s.lstrip().startswith("- "):
            if cur is not None:
                out[cur].append(s.lstrip()[2:].strip())
            continue
        k, _, v = s.partition(":")
        k = k.strip()
        v = v.strip()
        if v:
            out[k] = v
            cur = None
        else:
            out[k] = []
            cur = k
    return out


class Unmapped(Exception):
    pass


def norm(s):
    s = PHRE.sub("<P>", s)
    s = re.sub(r'\b\d{1,3}(?:\.\d{1,3}){3}\b', '<IP>', s)
    return re.sub(r'\b\d+\b', '<N>', s)


def lit_len(tok):
    if not re.fullmatch(r'\d+\.\d+\.\d+\.\d+', tok):
        return None
    v = 0
    for o in tok.split("."):
        v = (v << 8) | int(o)
    for inv in (False, True):
        x = (~v & 0xFFFFFFFF) if inv else v
        b = bin(x)[2:].zfill(32)
        if re.fullmatch(r'1*0*', b):
            return str(b.count("1"))
    return None


def keys_of(x, out):
    if isinstance(x, dict):
        for k, v in x.items():
            out.add(k)
            keys_of(v, out)
    elif isinstance(x, list):
        for i in x:
            keys_of(i, out)


class Conv:
    def __init__(self, params, values=None):
        self.params = params
        self.values = values or {}
        self.lo0_ip = None
        self.lo0 = {}
        self.notes = []
        self.cm_seq = collections.defaultdict(int)

    def note(self, msg):
        self.notes.append(msg)

    def plen(self, tok):
        n = lit_len(tok)
        if n is not None:
            return n
        m = PHRE.fullmatch(tok)
        if not m:
            raise Unmapped("mask/network token not resolvable: " + tok)
        name = m.group(1)
        p = self.params.get(name)
        if p and p.get("type") == "subnet_attr":
            sub = p["subnet"]
            for k, v in self.params.items():
                if v.get("type") == "subnet_attr" and v.get("subnet") == sub and v.get("attr") == "prefixlen":
                    return "{{%s}}" % k
            new = sub.lstrip("_") + "_prefix_len"
            self.params[new] = {"type": "subnet_attr", "subnet": sub, "attr": "prefixlen"}
            return "{{%s}}" % new
        base = re.sub(r"_(mask|wildcard|network)$", "", name)
        if base + "_prefix_len" in self.params:
            return "{{%s_prefix_len}}" % base
        if p and p.get("type") == "choice":
            opts = p.get("options")
            vals = self.values.get(opts) if isinstance(opts, str) else opts
            if isinstance(vals, list) and len(vals) == 1:
                n = lit_len(str(vals[0]))
                if n is not None:
                    return n
            raise Unmapped("choice mask param (multi-option, pairing unknown): " + tok)
        raise Unmapped("no prefix_len sibling: " + tok)

    @staticmethod
    def blocks(lines):
        out = []
        for ln in lines:
            if not ln.strip() or ln.strip() == "!":
                continue
            if ln[0] not in " \t" or not out:
                out.append([ln.strip(), []])
            else:
                out[-1][1].append(ln.strip())
        return out

    def iface_block(self, head, kids):
        name = head.split(None, 1)[1]
        out, ip, ipaddr = [], None, None
        for k in kids:
            m = re.fullmatch(r'ip address (\S+) (\S+)', k)
            if m:
                ipaddr = m.group(1)
                ip = "%s/%s" % (ipaddr, self.plen(m.group(2)))
            elif k in IFACE_DROP or k == "no shutdown":
                continue
            elif re.fullmatch(r'ip vrf forwarding \S+', k):
                out.append("vrf " + k.split()[-1])   # FRR: `vrf NAME` under interface
            elif k == "mpls ip":
                self.note("mpls ip dropped (no MPLS in FRR env): check goal/test")
            else:
                raise Unmapped("interface child: " + norm(k))
        if name == "Loopback0":
            out.append("config interface ip remove Loopback0 " + DEFAULT_LO0)
            if ip:
                self.lo0_ip = ipaddr
        elif name.startswith("Loopback"):
            out.append("config loopback add " + name)
        if ip:
            out.append("config interface ip add %s %s" % (name, ip))
        if name == "Loopback0" and ip:
            out += ["route-map RM_SET_SRC permit 10", "set src " + ipaddr, "exit"]
        if "no shutdown" in kids and not name.startswith("Loopback"):
            out.append("config interface startup " + name)
        return out

    def ospf_block(self, head, kids):
        if " vrf " in head:
            raise Unmapped("block: " + norm(head))
        out = ["router ospf"]
        for k in kids:
            if k in DROP:
                continue
            if any(re.fullmatch(p, k) for p in OSPF_DROP):
                self.note("ospf '%s' dropped (metric semantics may differ): check goal/test" % norm(k))
                continue
            m = re.fullmatch(r'network (\S+) (\S+) area (\S+)', k)
            if m:
                out.append("network %s/%s area %s" % (m.group(1), self.plen(m.group(2)), m.group(3))); continue
            m = re.fullmatch(r'router-id (\S+)', k)
            if m:
                out.append("ospf router-id " + m.group(1)); continue
            m = re.fullmatch(r'redistribute bgp \S+(?: subnets)?( metric \d+)?', k)
            if m:
                out.append("redistribute bgp" + (m.group(1) or "")); continue
            if re.fullmatch(r'passive-interface \S+|redistribute (connected|static)', k):
                out.append(k); continue
            if re.fullmatch(r'area \S+ authentication( (message-digest|simple))?', k):
                out.append(k); continue
            raise Unmapped("ospf child: " + norm(k))
        return out + ["exit"]

    def bgp_block(self, head, kids):
        glob_, grp, peer, nets, af, has_rid = [], [], [], [], [], False
        for k in kids:
            if k in DROP:
                continue
            if any(re.fullmatch(p, k) for p in BGP_PASS):
                has_rid |= k.startswith("bgp router-id")
                glob_.append(k); continue
            if any(re.fullmatch(p, k) for p in BGP_DROP):
                self.note("bgp '%s' dropped (no FRR equivalent): check goal/test" % norm(k))
                continue
            m = re.fullmatch(r'network (\S+) mask (\S+)( backdoor)?', k) or re.fullmatch(r'network (\S+)()', k)
            if m:
                tok = m.group(2) or m.group(1)       # classful form: use the subnet's own length
                bd = " backdoor" if (m.lastindex or 0) >= 3 and m.group(3) else ""
                nets.append("network %s/%s%s" % (m.group(1), self.plen(tok), bd)); continue
            m = re.fullmatch(r'aggregate-address (\S+) (\S+)((?: as-set| summary-only)*)', k)
            if m:
                flags = m.group(3)
                if "as-set" in flags and "summary-only" in flags:
                    flags = " summary-only as-set"        # order FRR 8.4.4 accepts
                af.append("aggregate-address %s/%s%s" % (m.group(1), self.plen(m.group(2)), flags)); continue
            m = re.fullmatch(r'redistribute (connected|static)|redistribute ospf \d+', k)
            if m:
                af.append(k if m.group(1) else "redistribute ospf"); continue
            m = re.fullmatch(r'maximum-paths( ibgp)? \d+', k)
            if m:
                af.append(k); continue
            m = re.fullmatch(r'neighbor (\S+) (.+)', k)
            if m:
                who, rest = m.groups()
                if rest == "activate":
                    continue
                lm = re.fullmatch(r'local-as (\d+)( no-prepend)?( replace-as)?( dual-as)?', rest)
                if lm:
                    if lm.group(4):
                        self.note("dual-as dropped (no FRR equivalent): check goal/test")
                    grp.append("neighbor %s local-as %s%s%s" % (who, lm.group(1), lm.group(2) or "", lm.group(3) or ""))
                    continue
                if any(re.fullmatch(p, rest) for p in NBR_DROP):
                    self.note("neighbor '%s' dropped (no FRR equivalent): check goal/test" % norm(rest))
                    continue
                if re.fullmatch(NBR_GLOBAL, rest):
                    (peer if IS_IP.fullmatch(who) else grp).append(k); continue
                if any(re.fullmatch(p, rest) for p in NBR_AF):
                    line = "neighbor %s %s" % (who, rest.replace("remove-private-as", "remove-private-AS"))
                    (peer if IS_IP.fullmatch(who) else grp).append(line); continue
            raise Unmapped("bgp child: " + norm(k))
        # FRR requires peer-group definition (incl. remote-as) before any peer is
        # assigned to it, so emit group lines first.
        out = [head]
        if not has_rid and self.lo0_ip:
            out.append("bgp router-id " + self.lo0_ip)
        out.append("no bgp ebgp-requires-policy")
        out += glob_
        assignments = [l for l in peer if re.fullmatch(r'neighbor \S+ peer-group \S+', l)]
        out += grp + assignments + [l for l in peer if l not in assignments]
        if nets or af:
            out += ["address-family ipv4 unicast"] + nets + af + ["exit-address-family"]
        return out + ["exit"]

    def policy_or_top(self, head, kids):
        if head in DROP:
            return "route", []
        m = re.fullmatch(r'route-map (\S+) (permit|deny) (\S+)', head)
        if m:
            out = [head]
            for k in kids:
                if not re.fullmatch(RM_KID, k):
                    raise Unmapped("route-map child: " + norm(k))
                out.append(k)
            return "policy", out + ["exit"]
        if kids:
            raise Unmapped("block: " + norm(head))
        m = re.fullmatch(r'access-list (\S+) (permit|deny) (\S+)(?: (\S+))?', head)
        if m:
            n, act, net, wild = m.groups()
            return "policy", ["access-list %s %s %s/%s" % (n, act, net, self.plen(wild) if wild else "32")]
        if re.fullmatch(r'ip prefix-list \S+ seq \d+ (permit|deny) \S+( (le|ge) \d+)*', head):
            return "policy", [head]
        m = re.fullmatch(r'ip as-path access-list (\S+) (permit|deny) (.+)', head)
        if m:
            return "policy", ["bgp as-path access-list %s %s %s" % m.groups()]
        m = re.fullmatch(r'ip community-list(?: (standard|expanded))? (\S+) (permit|deny) (.+)', head)
        if m:
            # FRR 8.4.4: bgp community-list <type> <name> seq N <action> <comm>
            self.cm_seq[m.group(2)] += 1
            return "policy", ["bgp community-list %s %s seq %d %s %s"
                              % (m.group(1) or "standard", m.group(2), 5 * self.cm_seq[m.group(2)],
                                 m.group(3), m.group(4))]
        m = re.fullmatch(r'ip route (\S+) (\S+) (\S+)(?: (\d+))?', head)
        if m:
            gw = "null0" if m.group(3).lower() == "null0" else m.group(3)
            return "route", ["ip route %s/%s %s%s" % (m.group(1), self.plen(m.group(2)), gw, " " + m.group(4) if m.group(4) else "")]
        raise Unmapped("top-level: " + norm(head))

    def device(self, lines, startup, dev):
        ifc, pol, rtr, rte = [], [], [], []
        self.lo0_ip = None if startup else self.lo0.get(dev)
        for head, kids in self.blocks(lines):
            if head.startswith("interface "):
                if not startup:
                    raise Unmapped("interface block in groundtruth")
                ifc += self.iface_block(head, kids)
                if self.lo0_ip:
                    self.lo0[dev] = self.lo0_ip
            elif head.startswith("router ospf"):
                rtr += self.ospf_block(head, kids)
            elif head.startswith("router bgp"):
                rtr += self.bgp_block(head, kids)
            elif re.fullmatch(r'router eigrp \d+', head):
                out = [head]
                for k in kids:
                    if k in DROP:
                        continue
                    m = re.fullmatch(r'network (\S+)(?: (\S+))?', k)
                    if m:
                        tok = m.group(2) or m.group(1)   # classful form: use the network's own length
                        out.append("network %s/%s" % (m.group(1), self.plen(tok))); continue
                    if re.fullmatch(r'redistribute (connected|static|ip route)', k):
                        out.append(k); continue
                    raise Unmapped("eigrp child: " + norm(k))
                rtr += out + ["exit"]
            elif head.startswith("router "):
                raise Unmapped("block: " + norm(head))
            else:
                kind, out = self.policy_or_top(head, kids)
                (pol if kind == "policy" else rte).extend(out)
        return ifc + pol + rtr + rte

    # ---- testcase commands -> (device command, parser kind)
    def cmd(self, c):
        c = c.strip()
        v = lambda s: 'sudo vtysh -c "%s"' % s
        m = re.fullmatch(r'show ip bgp neighbors(?: (\S+))?', c)
        if m: return v("show ip bgp neighbors%s json" % (" " + m.group(1) if m.group(1) else "")), "nbr"
        if c == "show ip bgp":
            return v("show ip bgp"), "bgp_table"       # text: carries 's' suppressed flag
        if c in ("show ip bgp summary", "show ip protocols | section bgp"):
            return v("show ip bgp summary json"), "bgp_summary"
        m = re.fullmatch(r'show ip bgp (\S+) (\S+)', c)
        if m and m.group(2) != "summary": return v("show ip bgp %s/%s json" % (m.group(1), self.plen(m.group(2)))), "bgp_prefix"
        m = re.fullmatch(r'show ip bgp (\S+/\S+)', c)
        if m: return v("show ip bgp %s json" % m.group(1)), "bgp_prefix"
        if c == "show ip route": return v("show ip route"), "rt"
        m = re.fullmatch(r'show ip route (\S+) (\S+)', c)
        if m and not c.startswith("show ip route vrf"):
            return v("show ip route %s/%s" % (m.group(1), self.plen(m.group(2)))), "rt_detail"
        m = re.fullmatch(r'show ip route (\S+)', c)
        if m and m.group(1) not in ("vrf", "connected", "static", "protocol"):
            return v("show ip route " + m.group(1)), "rt_detail"
        if c == "show ip ospf neighbor": return v("show ip ospf neighbor json"), "ospf_nbr"
        if c == "show ip eigrp neighbors": return v("show ip eigrp neighbors"), "eigrp_nbr"
        if c == "show ip eigrp topology": return v("show ip eigrp topology"), "eigrp_topo"
        if c == "show ip ospf interface brief": return v("show ip ospf interface json"), "ospf_if"
        if c == "show ip prefix-list": return v("show ip prefix-list"), "pfxlist"
        m = re.fullmatch(r'show ip community-list(?: (\S+))?', c)
        if m: return v("show bgp community-list"), "cmlist"
        if c == "show route-map": return v("show route-map"), "rmap"
        m = re.fullmatch(r'ping (\S+)(?: source (\S+))?', c)
        if m: return "ping -c 5 %s%s" % ("-I %s " % m.group(2) if m.group(2) else "", m.group(1)), "ping"
        m = re.fullmatch(r'show running-config \| include (.+)', c)
        if m:
            # FRR vtysh supports the same pipe; include is a regex in both IOS and FRR
            return v("show running-config | include %s" % m.group(1)), "line"
        m = re.fullmatch(r'show running-config \| section (ip route)', c)
        if m:
            # parsed static routes: {destination, nexthop_ip, distance}
            return v("show running-config | include ^ip route"), "iproute_cfg"
        raise Unmapped("testcase cmd: " + norm(c))


def convert(d, values=None):
    d = copy.deepcopy(d)
    cv = Conv(d["params"], values)
    why = []
    for node in d.get("topology_attributes", {}).get("nodes", {}).values():
        node["type"] = "SONiC-VS"
    for sec, startup in (("startup_configs", True), ("groundtruth_configs", False)):
        for dev, lines in d.get(sec, {}).items():
            try:
                d[sec][dev] = cv.device(lines, startup, dev)
            except Unmapped as e:
                why.append(str(e))
    for g in d.get("testcases", []):
        for t in g.get("testcase", []):
            try:
                t["command"], kind = cv.cmd(t["command"])
                ks = set(); keys_of(t.get("expected_result", {}), ks)
                missing = ks - META_KEYS - PARSER_KEYS[kind]
                if missing:
                    raise Unmapped("parser keys [%s]: %s" % (kind, ",".join(sorted(missing))))
            except Unmapped as e:
                why.append(str(e))
    notes = sorted(set(cv.notes))
    if GOAL_FLAGS.search(str(d.get("goals", ""))):
        notes.append("goal text mentions Cisco-specific wording: read it")
    return d, sorted(set(why)), notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="task")
    ap.add_argument("--dst", default="sonic-gen")
    ap.add_argument("--only", default=None, help="regex on task folder name")
    a = ap.parse_args()
    reasons = collections.Counter()
    ok = skipped = 0
    for p in sorted(glob.glob(os.path.join(a.src, "*", "*.json"))):
        folder = os.path.basename(os.path.dirname(p))
        if a.only and not re.search(a.only, folder):
            continue
        try:
            d = json.load(open(p))
        except Exception:
            continue
        if not isinstance(d, dict) or "groundtruth_configs" not in d:
            continue
        if "router bgp" not in json.dumps(d["startup_configs"]) + json.dumps(d["groundtruth_configs"]):
            continue
        out, why, notes = convert(d, load_yaml(os.path.join(os.path.dirname(p), "params.yaml")))
        if why:
            skipped += 1
            reasons.update(why)
            print("SKIP %-45s %d: %s" % (folder[:45], len(why), "; ".join(why[:2])))
            continue
        ok += 1
        dd = os.path.join(a.dst, folder)
        os.makedirs(dd, exist_ok=True)
        for f in os.listdir(os.path.dirname(p)):
            if not f.endswith(".json"):
                shutil.copy(os.path.join(os.path.dirname(p), f), dd)
        json.dump(out, open(os.path.join(dd, os.path.basename(p)), "w"), indent=4, ensure_ascii=False)
        print("OK   %-45s %s" % (folder[:45], ("REVIEW: " + " | ".join(notes)) if notes else ""))
    print("\nconverted=%d skipped=%d" % (ok, skipped))
    print("\n== unmapped shapes (count = #tasks) ==")
    for k, n in reasons.most_common():
        print("%3d  %s" % (n, k))


if __name__ == "__main__":
    main()
