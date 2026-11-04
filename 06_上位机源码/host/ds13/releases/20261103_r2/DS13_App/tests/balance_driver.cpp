#include "Vsim.h"
#include "verilated.h"
#include "balance_probes.h"
#include <array>
#include <cmath>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <cstdint>
double sc_time_stamp(){return 0;}
struct Sim {
 Vsim d; uint64_t cycle=0,mismatches=0,checks=0,run_cycles=0,pwm_high=0;
 std::array<uint64_t,NPROBES> bad{};
 uint64_t pin_bad=0,adc_bad=0,lastbatch=0,lastsample=0,lastfast=0,startfast=0,maxlat=0;
 uint64_t batches=0,samples=0,fasts=0,dones=0,nonzero_moto=0,nonzero_position=0;
 uint32_t lastad=0,lastpd=0,lastpp=0,adchanges=0,pdchanges=0,ppchanges=0;
 int qstate=0,rawpos=0; bool monitor=false,was_source=false; std::string stage="reset";
 void require(bool v,const char* msg){if(!v)throw std::runtime_error(std::string(msg)+" at cycle="+std::to_string(cycle)+" stage="+stage);}
 void compare(){
  if(!monitor)return;
  checks++;
  if(std::memcmp(d.ref_p.data(),d.dut_p.data(),NPROBES*sizeof(uint64_t))!=0){
   for(int i=0;i<NPROBES;i++){
    if(gated[i]&&!d.ref_p[P_core_base_seen])continue;
    if(d.ref_p[i]!=d.dut_p[i]){
     mismatches++;
     if(bad[i]++==0)std::cout<<"DIFF stage="<<stage<<" cycle="<<cycle<<" signal="<<names[i]<<" ref=0x"<<std::hex<<d.ref_p[i]<<" dut=0x"<<d.dut_p[i]<<std::dec<<" refwide="<<int64_t(d.ref_wide)<<" dutwide="<<int64_t(d.dut_wide)<<" reftarget="<<int64_t(d.ref_target)<<" duttarget="<<int64_t(d.dut_target)<<std::endl;
    }
   }
  }
  if(d.ref_pins!=d.dut_pins){if(pin_bad++==0)std::cout<<"DIFF PWM cycle="<<cycle<<" stage="<<stage<<" ref="<<int(d.ref_pins)<<" dut="<<int(d.dut_pins)<<std::endl;}
  if(d.ref_adc_clk!=d.dut_adc_clk)adc_bad++;
  if(d.ref_p[P_actual_balance_permission]){run_cycles++;pwm_high+=(d.ref_pins&1)!=0;nonzero_moto+=d.ref_p[P_moto]!=0;}
 }
 void cadence(){
  if(!monitor)return;
  const auto&p=d.ref_p;
  if(p[P_adc_batch_valid]){if(lastbatch)require(cycle-lastbatch==50000,"ADC batch period");lastbatch=cycle;batches++;}
  if(!p[P_source_rst_n]){lastsample=lastfast=lastad=lastpd=lastpp=0;was_source=false;return;}
  if(!was_source){lastsample=lastfast=lastad=lastpd=lastpp=0;was_source=true;}
  if(p[P_sample_en]){if(lastsample)require(cycle-lastsample==250000,"D launch period");lastsample=cycle;samples++;}
  if(p[P_core_fast_sample_en]){if(lastfast)require(cycle-lastfast==50000,"fast launch period");lastfast=cycle;startfast=cycle;fasts++;}
  if(p[P_core_fast_done]){auto latency=cycle-startfast;if(latency>maxlat)maxlat=latency;require(latency<50000,"fast deadline");}
  if(p[P_done]&&p[P_control_fast_p]){
   dones++; uint32_t ad=p[P_control_angle_d_epoch],pd=p[P_control_position_d_epoch],pp=p[P_control_position_p_epoch];
   if(lastad&&ad!=lastad){require(ad-lastad==5,"angle D epoch");adchanges++;}
   if(lastpd&&pd!=lastpd){require(pd-lastpd==25,"position D epoch");pdchanges++;}
   if(lastpp&&pp!=lastpp){require(pp-lastpp==5,"position P epoch");ppchanges++;}
   lastad=ad;lastpd=pd;lastpp=pp;
   nonzero_position+=p[P_position_p]!=0;
   require(p[P_angle_i]==0&&p[P_position_i]==0,"S1 integral term");
  }
 }
 void step(){d.clk=0;d.eval();cadence();d.clk=1;d.eval();cycle++;compare();}
 void cycles(uint64_t n){while(n--)step();}
 void ms(int n){cycles(uint64_t(n)*50000);}
 void pulse(int which){if(which==0)d.zero_cmd=1;if(which==1)d.run_cmd=1;if(which==2)d.stop_cmd=1;step();d.zero_cmd=d.run_cmd=d.stop_cmd=0;step();}
 void edge(int direction){qstate=(qstate+(direction>0?1:3))&3;static const int states[]={0,2,3,1};d.ea=(states[qstate]>>1)&1;d.eb=states[qstate]&1;rawpos+=direction;}
 void shift(int n){int sign=n<0?-1:1;for(int i=0;i<std::abs(n);i++){edge(sign);cycles(5000);}cycles(100);}
 void ready(){for(int i=0;!d.ref_p[P_source_ready]&&i<80;i++)ms(1);require(d.ref_p[P_source_ready]&&d.dut_p[P_source_ready],"source readiness");}
 void runcheck(){require(d.ref_p[P_mode]==3&&d.ref_p[P_actual_balance_permission],"reference RUN");require(d.dut_p[P_mode]==3&&d.dut_p[P_actual_balance_permission],"candidate RUN");}
 void waveform(int duration,int offset,int phase){
  // Each step is a physical quadrature pin transition. Input qualification,
  // kit scaling, ADC capture, feedback, and all schedules remain real RTL.
  for(int t=0;t<duration*50;t++){
   double tm=t/50.0;
   int wanted=offset+int(std::round(12*std::sin((tm+phase)*6.283185307179586/150.0)));
   if(rawpos<wanted)edge(1);else if(rawpos>wanted)edge(-1);
   d.adc=512+int(std::round(4*std::sin((tm+phase)*6.283185307179586/71.0)));
   cycles(1000);
  }
 }
 void test(int kit){
  d.rst_n=0;d.kit=kit;d.zero_cmd=d.run_cmd=d.stop_cmd=0;d.adc=512;d.ea=d.eb=0;cycles(10);d.rst_n=1;cycles(1000);monitor=true;
  stage="warm_adc";ms(4);stage="ZERO";pulse(0);ready();ms(40);
  const uint32_t gains[]={0x42571eb8,0,0x435223d7,0x418d5c29,0,0x441a270a};
  for(int j=0;j<6;j++)require(d.ref_bank[j]==gains[j]&&d.dut_bank[j]==gains[j],"explicit S1 binding");
  stage="RUN_at_zero";pulse(1);runcheck();waveform(150,0,0);
  stage="STOP_move_positive";pulse(2);d.adc=512;shift(23-rawpos);ms(40);
  require(!d.ref_p[P_actual_balance_permission]&&!d.dut_p[P_actual_balance_permission],"STOP inhibit");
  stage="RUN_displaced_positive";pulse(1);runcheck();waveform(150,23,0);
  stage="STOP_move_negative";pulse(2);d.adc=512;shift(-19-rawpos);ms(40);
  stage="RUN_displaced_negative";pulse(1);runcheck();waveform(150,-19,50);
  stage="reZERO";pulse(2);d.adc=512;pulse(0);ready();ms(40);
  stage="RUN_after_reZERO";pulse(1);runcheck();waveform(100,rawpos,0);pulse(2);ms(2);
  require(d.ref_p[P_fault]==0&&d.dut_p[P_fault]==0,"unexpected safety fault");
  require(samples>100&&fasts>600&&dones>600&&pdchanges>20&&ppchanges>100&&adchanges>100,"schedule coverage");
  require(run_cycles>20000000&&pwm_high>1000&&nonzero_moto>100000&&nonzero_position>100,"nonvacuous control/PWM coverage");
  std::cout<<"SUMMARY kit="<<kit<<" cycles="<<cycle<<" compared_cycles="<<checks<<" probes="<<NPROBES<<" differing_values="<<mismatches<<" pwm_mismatch_cycles="<<pin_bad<<" adc_clock_mismatches="<<adc_bad
   <<" ADC64_batches="<<batches<<" D_launches="<<samples<<" fast_launches="<<fasts<<" completed_fast="<<dones<<" angleD_updates="<<adchanges<<" posD_updates="<<pdchanges<<" posP_updates="<<ppchanges
   <<" max_fast_latency_clocks="<<maxlat<<" motor_permitted_cycles="<<run_cycles<<" pwm_high_cycles="<<pwm_high<<" nonzero_position_samples="<<nonzero_position<<std::endl;
  for(int i=0;i<NPROBES;i++)if(bad[i])std::cout<<"MISMATCH_COUNT "<<names[i]<<" "<<bad[i]<<std::endl;
  require(mismatches==0&&pin_bad==0&&adc_bad==0,"whole-system manual balance equivalence");
 }
};
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);try{for(int kit:{1,0}){Sim s;s.test(kit);s.d.final();}std::cout<<"PASS manual balance, both kits, no drawing, hardware_actions=0"<<std::endl;return 0;}catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<std::endl;return 1;}}
