#!/usr/bin/env python3
"""Extract a byte-exact replay bundle (combo-job + cmd data chunks) from a
capture, so we can re-send a known-good job from Linux. Touches no hardware.
Usage: prep_replay.py <capture.pcapng> <out.pkl>"""
import subprocess, re, sys, json, pickle

F, OUTP = sys.argv[1], sys.argv[2]
FILT=("usbll.pid==0xe1||usbll.pid==0x69||usbll.pid==0x2d||usbll.pid==0xb4||"
      "usbll.pid==0xc3||usbll.pid==0x4b||usbll.pid==0xd2||usbll.pid==0x96||usbll.pid==0x5a||usbll.pid==0x1e")
out=subprocess.run(["tshark","-r",F,"-Y",FILT,"-T","fields",
    "-e","usbll.pid","-e","usbll.endp","-e","usbll.data"],capture_output=True,text=True).stdout
OUT,IN,SETUP,PING=0xe1,0x69,0x2d,0xb4; DATA0,DATA1=0xc3,0x4b; ACK,NYET,NAK,STALL=0xd2,0x96,0x5a,0x1e
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
big=bytes(streams[("OUT","6")])

# combo-job command, verbatim (cmd json\n{...} up to the next 'cmd ' marker)
ci=big.find(b'"combo-job"'); s=big.rfind(b"cmd json\n",0,ci)
nxt=big.find(b"cmd ",ci); combo=big[s:nxt]
combo_json=json.loads(combo[len(b"cmd json\n"):])
print("combo-job (verbatim %d B):"%len(combo), json.dumps(combo_json)[:220])

# every cmd data chunk, verbatim (header + 4B field + EXTLEN payload)
chunks=[]
for m in re.finditer(rb"cmd data EXTLEN=(\d+)\n",big):
    L=int(m.group(1)); chunks.append(big[m.start():m.end()+4+L])
print(f"cmd data chunks: {len(chunks)}  sizes={[len(c) for c in chunks]}")
extsum=sum(int(re.match(rb"cmd data EXTLEN=(\d+)",c).group(1)) for c in chunks)
print(f"payload total (EXTLEN sum): {extsum}  (expect print+cut = 146634)")

pickle.dump({"combo":combo,"combo_json":combo_json,"chunks":chunks}, open(OUTP,"wb"))
print("saved ->", OUTP)
