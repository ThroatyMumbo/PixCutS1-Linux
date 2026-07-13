#!/usr/bin/env python3
"""Replay a byte-exact PixCut job bundle over Linux (interface 2, EP 0x06/0x86).
THIS MOVES THE MACHINE AND CONSUMES A SHEET.
Usage: replay_job.py <bundle.pkl>"""
import sys, json, time, pickle, re, struct
import usb.core, usb.util

VID,PID,IFACE,EP_OUT,EP_IN = 0x302C,0x3101,2,0x06,0x86
bundle=pickle.load(open(sys.argv[1],"rb"))

dev=usb.core.find(idVendor=VID,idProduct=PID)
if dev is None: sys.exit("PixCut not found")
if dev.is_kernel_driver_active(IFACE): dev.detach_kernel_driver(IFACE)
usb.util.claim_interface(dev,IFACE)

def rpc_read(tmo=15000):
    return bytes(dev.read(EP_IN,512,timeout=tmo))
def send_json(method,params,i):
    dev.write(EP_OUT, b"cmd json\n"+json.dumps({"method":method,"params":params,"id":i}).encode(), timeout=5000)
    return json.loads(rpc_read().decode("utf-8","replace"))

# 0) WAIT for ready (state 20) before submitting — the app always did.
#    Submitting during state 30 got the job discarded last time.
print("waiting for printer ready (state 20) ...")
ready=False
for _ in range(30):
    st=send_json("get-prop",["printer-state","printer-sub-state","printer-state-alerts"],1)
    print("  printer-state:", st.get("result"))
    if st.get("result",[None])[0]=="20": ready=True; break
    time.sleep(2)
if not ready:
    usb.util.release_interface(dev,IFACE)
    sys.exit("Printer never reached ready(20) -- media loaded? cover closed? Job NOT sent.")

# 1) announce the job (verbatim combo-job) -> job_id
print("\n>>> sending combo-job (MACHINE WILL START) ...")
dev.write(EP_OUT, bundle["combo"], timeout=5000)
resp=json.loads(rpc_read().decode("utf-8","replace"))
job_id=resp["result"]["job_id"]
print("job_id =", job_id)

# 2) stream the data chunks. CRITICAL: the 4-byte field after
#    'cmd data EXTLEN=N\n' is the job_id (LE uint32) -- re-stamp it with OUR
#    job_id, else the device discards the data (transfer-size stays 0).
def stamp(ch, jid):
    off = re.match(rb"cmd data EXTLEN=(\d+)\n", ch).end()
    return ch[:off] + struct.pack("<I", jid) + ch[off+4:]
print(f">>> streaming {len(bundle['chunks'])} data chunks (re-stamped to job {job_id}) ...")
for n,ch in enumerate(bundle["chunks"],1):
    dev.write(EP_OUT, stamp(ch, job_id), timeout=20000)
    ok=rpc_read().decode("utf-8","replace").strip()
    print(f"  chunk {n:2}/{len(bundle['chunks'])} ({len(ch)}B) -> {ok}")

# 3) poll job/printer state until it finishes (back to ready 20 after busy)
print("\n>>> polling progress ...")
saw_busy=False; i=100
for _ in range(80):
    ps=send_json("get-prop",["printer-state","printer-sub-state","printer-state-alerts"],i); i+=1
    ji=send_json("get-job-info",{"job-id":job_id},i); i+=1
    pr=ps.get("result",["?","",""])
    res=ji.get("result"); r=res[0] if isinstance(res,list) and res and isinstance(res[0],dict) else {}
    print(f"  printer-state={pr}  job-state={r.get('job-state')} sub={r.get('job-sub-state')} "
          f"reason={r.get('job-state-reason')} print-page={r.get('printing-page-number')} cut-progress={r.get('cutting-progress')}")
    if pr[0]=="40": saw_busy=True
    if saw_busy and pr[0]=="20":
        print("\n*** JOB COMPLETE (printer returned to ready) ***"); break
    time.sleep(3)
usb.util.release_interface(dev,IFACE)
