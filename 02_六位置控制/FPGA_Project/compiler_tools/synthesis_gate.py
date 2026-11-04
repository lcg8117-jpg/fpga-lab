"""Strict50MHz timing report parsers; source-independent, no hardware access."""
import html
import re
from decimal import Decimal

def rows(table: str) -> list[list[str]]:
    return [[html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
             for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, re.S | re.I)]
            for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", table, re.S | re.I)]

def syn_timing(text: str) -> dict:
    tables = [rows(t) for t in re.findall(r"<table\b[^>]*>(.*?)</table>", text, re.S | re.I)]
    clock = [t for t in tables if t and t[0] == ["NO.", "Clock Name", "Type", "Period", "Frequency(MHz)", "Rise", "Fall", "Source", "Master", "Object"]]
    perf = [t for t in tables if t and t[0] == ["NO.", "Clock Name", "Constraint", "Actual Fmax", "Logic Level", "Entity"]]
    if len(clock)!=1 or len(perf)!=1:
        raise ValueError("Missing SYN clock/Fmax table")
    cr=[r for r in clock[0][1:] if len(r)>6 and r[1]=="clk_50m"]
    pr=[r for r in perf[0][1:] if len(r)>3 and r[1]=="clk_50m"]
    if len(cr)!=1 or len(pr)!=1:raise ValueError("Missing/duplicate base clock")
    crow,prow=cr[0],pr[0]
    if crow[2]!="Base" or [Decimal(x) for x in crow[3:7]] != [20,50,0,10]:
        raise ValueError("Unexpected SYN base clock")
    if any(r[1] != "clk_50m" for r in clock[0][1:]):
        raise ValueError("Unexpected extra synthesis clocks")
    constraint = re.fullmatch(r"(\d+(?:\.\d+)?)\(MHz\)", prow[2])
    if prow[1] != crow[1] or not constraint or Decimal(constraint[1]) != 50:
        raise ValueError("Unexpected SYN frequency constraint")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\(MHz\)", prow[3])
    if not match or Decimal(match[1]) < 50:
        raise ValueError("SYN estimate fails 50 MHz")
    paths = [dict(t) for t in tables if t and t[0][0] == "Slack" and all(len(r) == 2 for r in t)]
    if not paths or any(Decimal(p["Slack"]) < 0 for p in paths):
        raise ValueError("Missing SYN paths or negative SYN setup slack")
    return {"passed": True, "scope": "Synthesis timing estimate only; final routed STA remains required",
            "fmax_mhz": float(match[1]), "clock_period_ns": 20,
            "worst_reported_setup_slack_ns": float(min(Decimal(p["Slack"]) for p in paths)), "reported_path_count": len(paths)}


def main():
    import json
    from pathlib import Path
    import sys
    import xml.etree.ElementTree as ET
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_]+", args.project):
        raise ValueError("Unsafe project stem")
    build = Path.cwd()
    proof = {"status": "FAIL", "scope": "Synthesis estimate only; routed acceptance is still required"}
    try:
        proof.update(project=args.project)
        proof.update(syn_timing((build / f"impl/gwsynthesis/{args.project}_syn.rpt.html").read_text(errors="replace")))
        resource = ET.parse(build / f"impl/gwsynthesis/{args.project}_syn_rsc.xml").getroot()
        counts = {name: int(resource.attrib[name].split("(")[0]) for name in ("T_Lut", "T_Alu", "T_Register")}
        proof["synthesis_resources"] = counts
        proof["projected_logic_lower_bound"] = counts["T_Lut"] + counts["T_Alu"]
        if proof["projected_logic_lower_bound"] > 54720:
            raise ValueError("Synthesis logic estimate exceeds device logic capacity")
        if counts["T_Register"] > 42000:
            raise ValueError("Synthesis register capacity exceeded")
        proof["status"] = "PASS"
        print("SYNTHESIS_GATE=PASS " + json.dumps(proof), flush=True)
        return 0
    except (ValueError, OSError, KeyError, ET.ParseError) as error:
        proof["reason"] = str(error)
        print("SYNTHESIS_GATE=FAIL " + str(error), flush=True)
        return 1
    finally:
        (build / "SYNTHESIS_GATE.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
