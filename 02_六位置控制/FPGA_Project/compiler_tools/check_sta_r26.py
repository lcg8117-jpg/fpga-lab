"""Reparse all applicable native Gowin STA categories and bind new build evidence.

Source-independent parser; accepting an R27 build requires build_native_r26.accept().
No synthesis, implementation, device, serial, BLE or JTAG operation is performed.
"""
from __future__ import annotations
import argparse
import hashlib
import html
import importlib.util
import json
import math
from pathlib import Path
import re
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
CATEGORIES={'setup':('Setup_Slack_Table','setup'),'hold':('Hold_Slack_Table','hold'),
 'recovery':('Recovery_Slack_Table','recovery'),'removal':('Removal_Slack_Table','removal'),
 'minimum_pulse_width':('MIN_PULSE_WIDTH_TABLE',None)}

def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
 return h.hexdigest()
def text(fragment):return html.unescape(re.sub(r'<[^>]+>',' ',fragment)).strip()
def rows(fragment):
 # Gowin1.9.12 emits one unclosed Frequency<td> in the Clock table.
 # The next explicit cell start is an unambiguous boundary; do not merge
 # frequency with Rise or silently parse just the first number of a cell.
 return [[text(c) for c in re.findall(r'<(?:td|th)\b[^>]*>(.*?)(?=</(?:td|th)>|<(?:td|th)\b|$)',r,re.I|re.S)]
  for r in re.findall(r'<tr\b[^>]*>(.*?)</tr>',fragment,re.I|re.S)]
def section(document,name):
 anchors=list(re.finditer(r'<a\b[^>]*\b(?:name|id)\s*=\s*[\"\x27]([^\"\x27]+)[\"\x27][^>]*>',document,re.I))
 matches=[(i,m) for i,m in enumerate(anchors) if m[1]==name]
 if len(matches)!=1:raise ValueError('Missing/duplicate STA section: '+name)
 i,m=matches[0];end=anchors[i+1].start() if i+1<len(anchors) else len(document)
 return document[m.start():end]
def table(document,name):
 part=section(document,name)
 match=re.search(r'<table\b[^>]*>(.*?)</table>',part,re.I|re.S)
 if not match:raise ValueError('Missing complete STA table: '+name)
 return rows(match[1]),part

def number(raw):
 value=float(raw)
 if not math.isfinite(value):raise ValueError('Non-finite native STA number: '+raw)
 return value

def timing(document):
 if '</html>' not in document[-1000:].lower():raise ValueError('Truncated native timing HTML')
 summary_rows,_=table(document,'STA_Tool_Run_Summary')
 summary={r[0]:r[1] for r in summary_rows if len(r)==2}
 needed=('Numbers of Endpoints Analyzed','Numbers of Setup Violated Endpoints','Numbers of Hold Violated Endpoints')
 if any(key not in summary for key in needed):raise ValueError('Incomplete STA endpoint summary')
 endpoints=int(summary[needed[0]])
 if endpoints<1000:raise ValueError('Incomplete analysis scope')
 for key in needed[1:]:
  if int(summary[key])!=0:raise ValueError('Native endpoint violation: '+key)
 clock_rows,_=table(document,'Clock_Report')
 clock_data=[r for r in clock_rows if r and r[0].isdigit()]
 if len(clock_data)!=1 or clock_data[0][1:3]!=['clk_50m','Base'] or [number(x) for x in clock_data[0][3:7]]!=[20,50,0,10]:
  raise ValueError('Expected sole original clk_50m at 20ns/50MHz')
 frequency_rows,_=table(document,'Max_Frequency_Report')
 frequency=[r for r in frequency_rows if r and r[0].isdigit()]
 if len(frequency)!=1 or frequency[0][1]!='clk_50m':raise ValueError('Incomplete native Fmax scope')
 target=number(frequency[0][2].split('(')[0]);fmax=number(frequency[0][3].split('(')[0])
 if target!=50 or fmax<50:raise ValueError('Final timing below original 50MHz target')
 result={'analyzed_endpoints':endpoints,'clock_period_ns':20.0,'clock_target_mhz':50.0,'final_fmax_mhz':fmax,'categories':{}}
 for kind,(anchor,switch) in CATEGORIES.items():
  part=section(document,anchor)
  plain=text(part)
  command='report_min_pulse_width' if switch is None else 'report_timing -'+switch
  if command not in plain:raise ValueError('Missing global worst-path report command: '+kind)
  limit_match=re.search(r'-(?:nworst|max_paths)\s+(\d+)',plain)
  if not limit_match or int(limit_match[1])<1:raise ValueError('Missing worst-path scope: '+kind)
  not_applicable=re.search(r'\bNo\s+(?:timing\s+)?paths?(?:\s+found)?\s+(?:to\s+report|available)|\bNo\s+(?:applicable|valid)\s+(?:timing\s+)?paths?\b',plain,re.I)
  try:reported,_=table(document,anchor)
  except ValueError:
   if not not_applicable:raise
   reported=[]
  data=[r for r in reported if r and re.fullmatch(r'\d+',r[0])]
  if not data:
   if not not_applicable:raise ValueError('Empty category has no explicit not-applicable evidence: '+kind)
   result['categories'][kind]={'applicable':False,'accepted':True,'minimum_slack_ns':None,
    'not_applicable_evidence':not_applicable[0],'violated_endpoints':None}
   continue
  if not reported or len(reported[0])<2 or 'slack' not in reported[0][1].lower():raise ValueError('Unknown slack table layout: '+kind)
  if len(data)>int(limit_match[1]):raise ValueError('Worst-path table exceeds requested scope: '+kind)
  slacks=[number(r[1]) for r in data]
  if slacks!=sorted(slacks):raise ValueError('Native global worst-path table is not ordered: '+kind)
  if min(slacks)<0:raise ValueError('Negative '+kind+' slack')
  endpoint_count=int(summary['Numbers of '+switch.title()+' Violated Endpoints']) if switch in ('setup','hold') else None
  category={'applicable':True,'accepted':True,'minimum_slack_ns':min(slacks),'reported_paths':len(data),
   'violated_endpoints':endpoint_count,'negative_reported_paths':sum(s<0 for s in slacks),
   'proof':'Global ordered worst-path report has nonnegative minimum; endpoint count is only asserted when native summary supplies it.',
   'report_anchor':anchor,'requested_worst_paths':int(limit_match[1])}
  if kind=='minimum_pulse_width':
   for r in data:
    if len(r)<7 or abs(number(r[2])-number(r[3])-number(r[1]))>.003:raise ValueError('Pulse actual/required/slack inconsistency')
   category['reported_pulse_types']=sorted({r[4] for r in data})
   category['scope']='Global n-worst pulse checks; unlisted high/low types are not declared absent.'
  result['categories'][kind]=category
 return result

def routing(pnr_log,physical_report,console):
 if 'Placement and routing completed' not in pnr_log or 'Bitstream generation completed' not in pnr_log:
  raise ValueError('Native routing and bitstream completion markers are required')
 lines=[ln for ln in (pnr_log+'\n'+physical_report+'\n'+console).splitlines() if re.search('unrout',ln,re.I)]
 counts=[]
 for line in lines:
  match=re.search(r'\b(\d+)\s+unrout(?:ed)?\b|\bunrouted(?:\s+(?:nets|connections))?\s*[:=]\s*(\d+)\b',line,re.I)
  if match:counts.append(int(next(x for x in match.groups() if x is not None)))
  elif re.search(r'\bno\s+unrouted\b',line,re.I):counts.append(0)
  else:raise ValueError('Unrecognized unrouted diagnostic: '+line)
 if any(counts):raise ValueError('Nonzero unrouted nets reported')
 if re.search(r'\bpartial\s+(?:route|routing|bitstream|implementation)|\brout(?:e|ing)\s+(?:failed|incomplete)|DesRoute failed',pnr_log+'\n'+physical_report,re.I):
  raise ValueError('Partial/failed native implementation diagnostic')
 return {'accepted':True,'placement_and_routing_completed':True,'bitstream_generation_completed':True,
  'explicit_unrouted_count':0 if counts else None,'unrouted_mentions':lines,
  'criterion':'Explicit native full routing and bitstream completion with no unrouted/partial-route warning; protected checker also reparses completion. A missing numeric count remains null, never fabricated as zero.',
  'numeric_zero_explicitly_reported':bool(counts)}

