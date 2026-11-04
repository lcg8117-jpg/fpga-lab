function [slow,fast,rawFrame,rawNow,guard,adcRaw,adcQ4]=balance_sensors(h,t,p)
% Equivalent circular medoid/window/map arithmetic on ideal ADC words.
% Electrical word timing and motor-pin quiet-cache selection remain unqualified.
capture=[59 10117 20175 30234 40292 50350 60409 70467 80525 90584 100642 110700 120759 130817 140875]/50e6;
frame=t-.003;ends=-5/50e6+floor((capture+5/50e6)/.001)*.001;
N=16;if p.fast,N=64;end
[uniqueEnds,~,ix]=unique(ends);batch=zeros(size(uniqueEnds));
for k=1:numel(uniqueEnds),batch(k)=batchAt(frame+uniqueEnds(k),N);end
batch=batch(ix);
slow=floor((sum(map(batch))+1)*4369/65536)-3085;
adcQ4=batchAt(t-5/50e6,N);fast=map(adcQ4)-3085;
raw=rawAt(frame+capture);guard=floor((sum(map(raw*16))+1)*4369/65536)-3085;
adcRaw=raw(end);rawFrame=round(p.encoder_sign*interp1(p.history_time,h(:,1),-.003,'linear')*1040/(2*pi));
rawNow=round(p.encoder_sign*h(end,1)*1040/(2*pi));
 function out=batchAt(te,n)
  times=te+((-n-7:0)*(.001/n));
  if n==16,times=te+(-17:0)*.0000625;lag=1;first=3;else,lag=4;first=9;end
  raw=rawAt(times);clean=zeros(1,n);
  for j=first:first+n-1
   r=[raw(j),raw(j-lag),raw(j-2*lag)];d=abs(r-r');d=min(d,1024-d);c=sum(d,2);
   if c(2)<=c(1)&&c(2)<=c(3),clean(j-first+1)=r(2);elseif c(1)<=c(3),clean(j-first+1)=r(1);else,clean(j-first+1)=r(3);end
  end
  d=clean-clean(1);d(d>511)=d(d>511)-1024;d(d< -512)=d(d< -512)+1024;
  total=sum(clean(1)+d);
  if n==64,q=floor(total/4);r=mod(total,4);q=q+(r>2 || r==2&&mod(q,2)==1);else,q=total;end
  out=mod(q,16384);
 end
 function out=rawAt(times)
  relative=times-t;joint=interp1(p.history_time,h(:,3)-h(:,5),relative,'linear');
  ni=times/.0000625-p.noise.first_index+1;
  j=floor(ni);fraction=ni-j;assert(all(j>=1&j<numel(p.noise.values)),'Noise time unavailable');
  nv=(1-fraction).*reshape(p.noise.values(j),size(times))+fraction.*reshape(p.noise.values(j+1),size(times));
  assert(all(isfinite(joint))&&all(isfinite(nv)),'Sensor stimulus history unavailable');
  out=mod(floor(p.physical_adc_datum_q4/16+joint/p.physical_adc_rad_per_raw+nv),1024);
 end
 function out=map(q)
  d=q-p.zero_q4;d(d>8191)=d(d>8191)-16384;d(d< -8192)=d(d< -8192)+16384;
  out=max(0,min(4095,3085+sign(d).*floor(abs(d)*9415911/2^25)));
 end
end
