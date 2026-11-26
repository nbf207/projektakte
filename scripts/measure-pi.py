"""Linux/Pi host, process and optional Docker measurements. Standard library only."""
import argparse
import json
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


def memory_values(file):
    values={}
    for line in file.read_text().splitlines():
        key,_,rest=line.partition(':')
        parts=rest.split()
        if parts and parts[0].isdigit(): values[key]=int(parts[0])
    return values


def collect(pids,containers):
    host=memory_values(Path('/proc/meminfo'))
    result={'at':datetime.now(timezone.utc).isoformat(),
            'host_kib':{key:host.get(key) for key in ('MemTotal','MemAvailable','SwapTotal','SwapFree')},
            'load':Path('/proc/loadavg').read_text().split()[:3], 'processes':[]}
    for pid in pids:
        try:
            status=memory_values(Path('/proc')/str(pid)/'status')
            item={'pid':pid,'rss_kib':status.get('VmRSS'),'swap_kib':status.get('VmSwap')}
            try: item['pss_kib']=memory_values(Path('/proc')/str(pid)/'smaps_rollup').get('Pss')
            except (OSError,PermissionError): item['pss_kib']=None
            result['processes'].append(item)
        except OSError: result['processes'].append({'pid':pid,'error':'not accessible or stopped'})
    if containers:
        try:
            run=subprocess.run(['docker','stats','--no-stream','--format','{{json .}}',*containers],
                               capture_output=True,text=True,timeout=15,check=True)
            result['containers']=[json.loads(line) for line in run.stdout.splitlines()]
        except (OSError,subprocess.SubprocessError,ValueError): result['containers_error']='docker stats unavailable'
    return result


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--scenario',required=True,help='baseline, idle, photos, paperless-ocr, export, restore')
    p.add_argument('--pid',type=int,action='append',default=[])
    p.add_argument('--container',action='append',default=[])
    p.add_argument('--seconds',type=int,default=300)
    p.add_argument('--interval',type=float,default=5)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if platform.system()!='Linux': p.error('Auf dem Linux/Pi-Zielhost ausführen; keine Pi-Werte simulieren.')
    if args.seconds<1 or args.interval<1: p.error('Positive Dauer und Intervall >=1 s erforderlich.')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf8') as out:
        out.write(json.dumps({'format':'projektakte-host-measurement-v1','scenario':args.scenario,
                              'machine':platform.machine(),'kernel':platform.release()})+'\n')
        deadline=time.monotonic()+args.seconds
        while time.monotonic()<deadline:
            out.write(json.dumps(collect(args.pid,args.container))+'\n');out.flush()
            time.sleep(min(args.interval,max(0,deadline-time.monotonic())))


if __name__=='__main__': main()
