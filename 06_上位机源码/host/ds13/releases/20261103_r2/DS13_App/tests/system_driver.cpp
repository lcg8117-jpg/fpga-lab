#include "Vsim.h"
#include "verilated.h"
#include <array>
#include <vector>
#include <deque>
#include <map>
#include <iostream>
#include <fstream>
#include <stdexcept>
#include <cstdint>
double sc_time_stamp(){return 0;}
using Bytes=std::vector<uint8_t>;
uint32_t u32(const Bytes&b,int o){return uint32_t(b[o])|(uint32_t(b[o+1])<<8)|(uint32_t(b[o+2])<<16)|(uint32_t(b[o+3])<<24);}
uint64_t u64(const Bytes&b,int o){return u32(b,o)|(uint64_t(u32(b,o+4))<<32);}
void put(Bytes&b,int o,uint32_t v){for(int j=0;j<4;j++)b[o+j]=v>>(8*j);}
uint16_t crc(const Bytes&b,int n){uint16_t c=0xffff;for(int j=0;j<n;j++){c^=b[j]<<8;for(int k=0;k<8;k++)c=c&0x8000?(c<<1)^0x1021:c<<1;}return c;}
std::array<uint32_t,6> S1={0x42571eb8,0,0x435223d7,0x418d5c29,0,0x441a270a};
struct Sim{
 Vsim d;uint64_t ticks=0,lastsample=0,lastbatch=0,lastobs=0,startobs=0,maxlatency=0;
 uint32_t samples=0,batches=0,observations=0,completed=0,checks=0,packets=0,controls=0,job_frames=0;
 uint32_t nextid=1,lastangle=0,lastpd=0,lastpp=0,anglechanges=0,pdchanges=0,ppchanges=0;
 uint32_t sampled_epoch=0,sampled_revision=0;uint16_t sampled_angle=0;
 std::array<uint32_t,6> sampled_gains={},sampled_base={};
 bool armed=false,prevtx=true,prevadc=false,prevpwm=false;uint64_t lastadc=0;uint32_t pwm_edges=0;int countdown=0,bit=-1;uint8_t byte=0;
 Bytes frame,lastcontrol;std::map<uint32_t,Bytes> acks;std::deque<Bytes> expected;
 std::ofstream wire{"uart_rx.bin",std::ios::binary};
 void ck(bool v,const char*msg){checks++;if(!v)throw std::runtime_error(std::string(msg)+" at ms="+std::to_string(d.ms)+" fault="+std::to_string(d.fault));}
 void got(uint8_t b){
  wire.put(b);frame.push_back(b);if(frame.size()<4)return;
  ck(frame[0]=='F'&&frame[1]=='D'&&frame[2]=='2',"UART magic");int n=frame[3]=='A'?80:256;
  if(int(frame.size())!=n)return;
  ck((frame[n-2]|frame[n-1]<<8)==crc(frame,n-2),"UART CRC");
  if(n==80){ck(u32(frame,72)==0x44533133,"ACK firmware");acks[u32(frame,8)]=frame;}
  else{
   ck(!expected.empty(),"unowned telemetry");auto e=expected.front();expected.pop_front();
   for(int k=0;k<254;k++)ck(e[k]==frame[k],"serialized snapshot changed");packets++;
   if(frame[3]=='T' && frame[6]&1){lastcontrol=frame;controls++;
    ck(u32(frame,32)==0x44533133&&u32(frame,36)==0x44535002,"T identity");
    if(frame[252])ck(frame[248]==1&&frame[249]==5&&frame[250]==5&&frame[251]==25&&frame[253]==64,"S1 timing metadata");
    if(frame[194]==6){job_frames++;ck(u32(frame,188)>0,"missing job ID");ck(int16_t(frame[192]|frame[193]<<8)>=-192&&int16_t(frame[192]|frame[193]<<8)<=192,"live target echo range");}
   }
  }frame.clear();
 }
 void step(){
  d.clk=0;d.eval();
  if(d.rst_n){
   if(d.sample){if(lastsample)ck(ticks-lastsample==250000,"completed-D sample cadence");lastsample=ticks;samples++;}
   if(d.batch){if(lastbatch)ck(ticks-lastbatch==50000,"ADC64 cadence");lastbatch=ticks;batches++;}
   if(d.obs){
    if(d.fast){if(lastobs)ck(ticks-lastobs==50000,"angle P cadence");lastobs=ticks;observations++;}
    startobs=ticks;armed=true;sampled_epoch=d.ms;sampled_revision=d.revision;sampled_angle=d.angle;
    for(int j=0;j<6;j++){sampled_gains[j]=d.gains[j];sampled_base[j]=d.base[j];}
   }
   if(d.done){completed++;ck(armed,"completion has no observation");armed=false;auto lat=ticks-startobs;if(lat>maxlatency)maxlatency=lat;
    ck(lat<50000,"control exceeds1ms");ck(d.integrals==0,"nonzero integral");
    if(d.fast){for(int j=0;j<6;j++)ck(d.applied[j]==sampled_gains[j],"applied gains differ from observation");
     if(lastangle&&d.angle_epoch!=lastangle){ck(d.angle_epoch-lastangle==5,"angle D epoch cadence");anglechanges++;}
     if(lastpd&&d.pos_d_epoch!=lastpd){ck(d.pos_d_epoch-lastpd==25,"position D epoch cadence");pdchanges++;}
     if(lastpp&&d.pos_p_epoch!=lastpp){ck(d.pos_p_epoch-lastpp==5,"position P epoch cadence");ppchanges++;}
     lastangle=d.angle_epoch;lastpd=d.pos_d_epoch;lastpp=d.pos_p_epoch;
    }
   }
  }
  d.clk=1;d.eval();ticks++;
  if(d.rst_n){
   ck(d.bad==0,"ownership/acquisition/scheduling invariant");ck(d.transport_errors==0,"transport overflow");
   if((d.pins&1)&&!prevpwm)pwm_edges++;prevpwm=d.pins&1;
   if(!d.zero_valid){lastsample=lastobs=0;lastangle=lastpd=lastpp=0;}
   if(d.packet_valid){Bytes p(256);for(int j=0;j<256;j++)p[j]=d.packet[j/4]>>(8*(j%4));expected.push_back(p);
    if(p[3]=='T'&&(p[6]&1)){
     ck(u32(p,16)==sampled_epoch&&u32(p,20)==sampled_revision,"T epoch/revision transaction");
     ck((p[54]|p[55]<<8)==sampled_angle,"T observed angle");
     for(int j=0;j<6;j++){ck(u32(p,152+4*j)==sampled_gains[j],"T actual bank");ck(u32(p,128+4*j)==sampled_base[j],"T manual bank");}
    }
   }
   if(d.adc_clock&&!prevadc){if(lastadc)ck(ticks-lastadc==16,"ADC clock");lastadc=ticks;}
   prevadc=d.adc_clock;
   if(bit<0){if(prevtx&&!d.tx){bit=0;countdown=37;byte=0;}}
   else if(--countdown==0){if(bit<8){byte|=d.tx<<bit;bit++;countdown=25;}else{ck(d.tx,"UART stop");got(byte);bit=-1;}}
   prevtx=d.tx;
  }
 }
 void cycles(uint64_t n){while(n--)step();}
 void sendbyte(uint8_t b){d.rx=0;cycles(25);for(int j=0;j<8;j++){d.rx=(b>>j)&1;cycles(25);}d.rx=1;cycles(25);}
 Bytes req(int op,int group=0,Bytes data={},int status=0){
  if(op==0x12)std::cout<<"RUN fixture ms="<<d.ms<<" ready="<<int(d.ready)<<" mode="<<int(d.mode)<<std::endl;
  Bytes p(48);p[0]='F';p[1]='D';p[2]='2';p[3]='C';p[4]=5;p[5]=op;p[6]=group;p[7]=data.size();auto id=nextid++;
  put(p,8,id);for(unsigned j=0;j<data.size();j++)p[12+j]=data[j];put(p,36,91);put(p,40,id);
  auto c=crc(p,46);p[46]=c;p[47]=c>>8;for(auto b:p)sendbyte(b);
  uint64_t deadline=ticks+500000;while(!acks.count(id)&&ticks<deadline)step();ck(acks.count(id),"ACK missing");auto a=acks.at(id);acks.erase(id);
  if(a[6]!=status)std::cerr<<"request op="<<op<<" group="<<group<<" status="<<int(a[6])<<" expected="<<status<<std::endl;
  ck(a[6]==status,"ACK status");return a;
 }
 Bytes config(int field,uint32_t value){Bytes p(5);p[0]=field;put(p,1,value);return p;}
 void ms(int n,bool heartbeat=true){while(n>0){int k=n>50?50:n;cycles(uint64_t(k)*50000);n-=k;if(heartbeat)req(1);}}
 void waitready(){for(int i=0;!d.ready&&i<100;i++)ms(1);ck(d.ready,"balance source ready");}
};
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);try{
 Sim s;s.d.rst_n=0;s.d.rx=1;s.d.keys=15;s.d.adc=512;s.d.ea=s.d.eb=0;s.cycles(8);s.d.rst_n=1;s.cycles(3000);
 for(int j=0;j<6;j++)s.ck(s.d.base[j]==S1[j],"reset S1 bits");s.ck(!s.d.permit&&!s.d.pins,"reset outputs");
 s.req(2);auto a=s.req(0x21,255);for(int j=0;j<6;j++)s.ck(u32(a,24+4*j)==S1[j],"independent S1 bank read");
 for(int op:{0x22,0x23,0x30,0x31,0x40,0x41,0x42,0x43,0x44,0x45})s.req(op,0,(op==0x30||op==0x45)?Bytes{1}:Bytes{},6);
 s.req(0x46,0,{7},6);s.req(0x32,252,{6},6);s.req(0x48,0,{},1);
 Bytes g(24);for(int j=0;j<6;j++)put(g,j*4,S1[j]);put(g,4,0x3f800000);s.req(0x20,255,g,3);
 s.req(0x33,254,s.config(5,1));a=s.req(0x32,254,{6});s.ck(u32(a,48)==1040,"IP570 read");s.req(0x33,254,s.config(5,0));
 s.ms(105);s.ck(!s.observations,"no fabricated preZERO control");s.req(0x11);s.waitready();s.ms(40);s.ck(s.d.fast,"fast P selected before RUN");
 s.req(0x12);s.ck(s.d.mode==3&&s.d.permit,"manual RUN");s.d.adc=513;s.ms(35);
 // A valid manual write adopts atomically, then return exactly to S1.
 for(int j=0;j<6;j++)put(g,j*4,S1[j]);put(g,0,0x42580000);s.req(0x20,255,g);s.ms(40);s.ck(s.d.applied[0]==0x42580000,"manual bank adopted");
 put(g,0,S1[0]);s.req(0x20,255,g);s.ms(40);for(int j=0;j<6;j++)s.ck(s.d.applied[j]==S1[j],"restored exact S1");
 s.d.adc=512;s.ms(100);
 auto before=s.completed;a=s.req(0x48);uint32_t job=u32(a,8);
 auto point=[&](uint32_t token,int32_t target,int status=0){Bytes b(8);put(b,0,token);put(b,4,uint32_t(target));return s.req(0x49,0,b,status);};
 auto end=[&](uint32_t token,int status=0){Bytes b(4);put(b,0,token);return s.req(0x4a,0,b,status);};
 point(job+1,20,1);point(job,193,1);a=s.req(0x48);job=u32(a,8);
 for(int j=0;j<25;j++){point(job,j<12?j:24-j);s.ms(20);s.ck(s.d.mode==3&&s.d.permit,"live stream interrupted balance");}
 s.ck(s.d.job_id==job&&s.d.play_time>=500&&s.d.play_state==6,"live identity/time");
 s.ck(int64_t(s.d.refpos)!=0,"no motion before drawing released");s.ck(s.completed>before+400,"live stream blocked controller");
 for(int j=0;j<12;j++){int q=j%4;s.d.ea=(q==0||q==1);s.d.eb=(q==1||q==2);s.cycles(100);}
 s.ms(20);s.ck(int64_t(s.d.actual)!=0,"encoder edges absent from real position");
 end(job+1,1);end(job);point(job,20,1);s.ck(s.d.permit,"release removed balance");s.ms(50);
 s.ck(s.job_frames>60,"live telemetry coverage");
 // Re-press while the previous reference is braking/settling: no physical-stillness gate.
 a=s.req(0x48);job=u32(a,8);point(job,-15);s.ms(30);end(job);
 a=s.req(0x48);job=u32(a,8);point(job,15);s.ms(30);
 s.ck(s.d.play_state==6 && s.d.permit,"new stroke while unsettled");end(job);
 // Return to a fresh zero through explicit manual controls only, then test missing points with live heartbeat.
 s.req(0x10);s.req(0x11);s.waitready();s.ms(100);s.req(0x12);s.ms(100);
 a=s.req(0x48);job=u32(a,8);point(job,10);s.ms(220);
 s.ck(s.d.play_state!=6&&s.d.permit,"missing-point lease must stop drawing but retain balance");point(job,20,1);
 s.ck(s.pwm_edges>10,"actual PWM activity coverage");
 s.req(0x10);s.ck(!s.d.permit&&!s.d.pins,"STOP physical outputs");s.waitready();s.req(0x12);s.ck(s.d.permit,"ESTOP fixture must be actively balancing");
 s.d.keys=7;s.d.eval();s.ck(!s.d.permit&&!s.d.pins,"physical ESTOP immediate inhibit before next clock");
 s.cycles(10);s.ck(s.d.fault==3,"physical ESTOP fault latch");s.d.keys=15;s.cycles(12);s.req(0x13);s.ms(10);s.waitready();
 s.req(0x12);s.ck(s.d.permit,"watchdog fixture active");s.ms(510,false);s.ck(!s.d.permit&&!s.d.pins,"watchdog STOP");
 s.req(0x10);
 s.cycles(250000);s.ck(s.anglechanges>20&&s.pdchanges>5&&s.ppchanges>20,"PID epoch coverage");
 s.ck(s.controls>100&&s.samples>100&&s.observations>500,"full chain coverage");
 std::cout<<"PASS full_system checks="<<s.checks<<" ticks="<<s.ticks<<" sample5ms="<<s.samples<<" ADC64_1ms="<<s.batches<<" fastP_1ms="<<s.observations<<" completions="<<s.completed<<" wire_packets="<<s.packets<<" coherent="<<s.controls<<" job_frames="<<s.job_frames<<" pwm_edges="<<s.pwm_edges<<" max_control_clocks="<<s.maxlatency<<" hardware_actions=0"<<std::endl;
 s.d.final();return 0;
 }catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<std::endl;return 1;}}
