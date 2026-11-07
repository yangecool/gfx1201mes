
import ctypes as C, fcntl, os, select, struct, datetime, json, time
class Addr(C.Structure):
 _fields_=[('addr_type',C.c_int),('channel',C.c_short),('lun',C.c_ubyte)]
class Msg(C.Structure):
 _fields_=[('netfn',C.c_ubyte),('cmd',C.c_ubyte),('data_len',C.c_ushort),('data',C.c_void_p)]
class Req(C.Structure):
 _fields_=[('addr',C.c_void_p),('addr_len',C.c_uint),('msgid',C.c_long),('msg',Msg)]
class Recv(C.Structure):
 _fields_=[('recv_type',C.c_int),('addr',C.c_void_p),('addr_len',C.c_uint),('msgid',C.c_long),('msg',Msg)]
def ioctl_num(direction,nr,size):return (direction<<30)|(size<<16)|(ord('i')<<8)|nr
fd=os.open('/dev/ipmi0',os.O_RDWR|os.O_NONBLOCK)
seq=0
def query(netfn,cmd,data=b''):
 global seq
 assert (netfn,cmd) in {(6,1),(10,64),(10,67),(10,72)},'Only read commands allowed'
 seq+=1
 addr=Addr(0x0c,0x0f,0)
 payload=C.create_string_buffer(data or b'\x00')
 req=Req(C.addressof(addr),C.sizeof(addr),seq,Msg(netfn,cmd,len(data),C.addressof(payload)))
 fcntl.ioctl(fd,ioctl_num(2,13,C.sizeof(Req)),bytes(req))
 deadline=time.monotonic()+5
 while time.monotonic()<deadline:
  if not select.select([fd],[],[],max(0,deadline-time.monotonic()))[0]:break
  rb=C.create_string_buffer(1024);ab=C.create_string_buffer(64)
  recv=Recv(0,C.addressof(ab),64,0,Msg(0,0,1024,C.addressof(rb)))
  buf=bytearray(bytes(recv));fcntl.ioctl(fd,ioctl_num(3,12,C.sizeof(Recv)),buf,True)
  recv=Recv.from_buffer_copy(buf)
  if recv.msgid==seq and recv.msg.cmd==cmd:return rb.raw[:recv.msg.data_len]
 raise TimeoutError('IPMI read timed out')
def iso(ts):return datetime.datetime.fromtimestamp(ts,datetime.timezone.utc).isoformat()
clock=query(10,72);now=time.time()
print(json.dumps({'type':'clock','host_utc':iso(now),'bmc_utc':iso(struct.unpack('<I',clock[1:5])[0]),'difference_seconds':struct.unpack('<I',clock[1:5])[0]-now,'raw':clock.hex()}))
info=query(10,64);print(json.dumps({'type':'sel-info','raw':info.hex(),'entries':struct.unpack('<H',info[2:4])[0]}))
last=query(10,67,struct.pack('<HHBB',0,65535,0,255))
if last[0]!=0:raise RuntimeError(last.hex())
lastid=struct.unpack('<H',last[3:5])[0]
for recid in range(max(1,lastid-69),lastid+1):
 r=query(10,67,struct.pack('<HHBB',0,recid,0,255))
 if r[0]!=0:
  print(json.dumps({'type':'entry-error','record_id':recid,'raw':r.hex()}));continue
 rec=r[3:19]
 obj={'type':'entry','record_id':struct.unpack('<H',rec[:2])[0],'raw':rec.hex(),'record_type':rec[2]}
 if rec[2]==2:
  obj.update(timestamp_utc=iso(struct.unpack('<I',rec[3:7])[0]),generator=rec[7:9].hex(),sensor_type=hex(rec[10]),sensor_number=rec[11],event_type=hex(rec[12]),event_data=rec[13:16].hex())
 print(json.dumps(obj))
os.close(fd)
