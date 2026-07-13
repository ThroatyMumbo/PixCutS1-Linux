#!/usr/bin/env python3
"""Extract a PixCut print+cut job from a Cynthion pcapng.
Usage: extract_job.py <capture.pcapng> [out_prefix]
Handles USB2 HS PING/NYET flow control (DATA delivered on ACK or NYET)."""
import subprocess, re, sys, json

F = sys.argv[1]
PREFIX = sys.argv[2] if len(sys.argv)>2 else "job"

FILT=("usbll.pid==0xe1||usbll.pid==0x69||usbll.pid==0x2d||usbll.pid==0xb4||"
      "usbll.pid==0xc3||usbll.pid==0x4b||usbll.pid==0xd2||usbll.pid==0x96||usbll.pid==0x5a||usbll.pid==0x1e")
out=subprocess.run(["tshark","-r",F,"-Y",FILT,"-T","fields",
    "-e","usbll.pid","-e","usbll.endp","-e","usbll.data"],capture_output=True,text=True).stdout
OUT,IN,SETUP,PING=0xe1,0x69,0x2d,0xb4
DATA0,DATA1=0xc3,0x4b; ACK,NYET,NAK,STALL=0xd2,0x96,0x5a,0x1e

cur=(None,None); pending=None; streams={}
for line in out.splitlines():
    p=line.split("\t")
    try: pid=int(p[0],16)
    except: continue
    endp=p[1] if len(p)>1 else ""; data=(p[2].replace(":","") if len(p)>2 else "")
    if pid in (OUT,IN,SETUP): cur=("OUT" if pid==OUT else "IN" if pid==IN else "SETUP",endp)
    elif pid==PING: cur=("OUT",endp)
    elif pid in (DATA0,DATA1): pending=(cur[0],cur[1],bytes.fromhex(data) if data else b"")
    elif pid in (ACK,NYET,NAK,STALL):
        if pending is not None:
            if pid in (ACK,NYET): streams.setdefault((pending[0],pending[1]),bytearray()).extend(pending[2])
            pending=None

big=bytes(streams.get(("OUT","6"),b""))

print("=== JSON methods sent (host -> device) ===")
from collections import Counter
methods=Counter()
for m in re.finditer(rb"cmd json\n(\{.*?\})(?=cmd |$)",big,re.S):
    try: j=json.loads(m.group(1))
    except: continue
    meth=j.get("method","?"); methods[meth]+=1
    if meth in ("combo-job","print-job","cut-job") or methods[meth]==1:
        print(f"  {json.dumps(j)[:300]}")
print("method counts:",dict(methods))

stream=bytearray(); extlens=[]
for m in re.finditer(rb"cmd data EXTLEN=(\d+)\n",big):
    L=int(m.group(1)); extlens.append(L); p=m.end()+4; stream+=big[p:p+L]
print(f"\ndata chunks={len(extlens)} total={len(stream)} bytes")

soi=stream.find(b"\xff\xd8\xff")
cut=bytes(stream[:soi]); jpg=bytes(stream[soi:])
open(f"{PREFIX}_cut.hpgl","wb").write(cut)
open(f"{PREFIX}_print.jpg","wb").write(jpg)
print(f"cut={len(cut)}B -> {PREFIX}_cut.hpgl   print(jpeg)={len(jpg)}B -> {PREFIX}_print.jpg")
print(f"jpeg SOI={jpg[:3].hex()} EOI={jpg[-2:].hex()}")
