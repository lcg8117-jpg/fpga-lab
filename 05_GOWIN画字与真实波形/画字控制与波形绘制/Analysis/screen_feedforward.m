function screen_feedforward()
root=fileparts(mfilename('fullpath'));cd(root);diary('screen_feedforward.log');
rows=struct([]);slot=0;
for lane=1:2
 word='GOWIN';if lane==2,word='EasyStart';end
 data=jsondecode(fileread(fullfile(root,'..','C5_Baseline','data',['Word_' word '.json'])));
 baseline=readtable(fullfile(root,'..','C5_Baseline','results',sprintf('baseline_lane%d.csv',lane)));
 for tempo=[1 2]
  stop=60*tempo;time=(0:.005:stop)';q=zeros(size(time));points=double(data.points(:));stepCount=20*tempo;
  sequence=zeros((numel(points)-3)*stepCount,1);cursor=0;
  for s=1:numel(points)-3
   p=points(s:s+3);a=p(1)+4*p(2)+p(3);b=3*(p(3)-p(1));c=3*(p(1)-2*p(2)+p(3));d=-p(1)+3*p(2)-3*p(3)+p(4);
   for j=1:stepCount
    cursor=cursor+1;
    sequence(cursor)=round((a*stepCount^3+b*stepCount^2*j+c*stepCount*j^2+d*j^3)*65536/(6*stepCount^3*16));
   end
  end
  q(103:102+numel(sequence))=sequence;
  reference=floor(q/65536);
  if tempo==1,assert(isequal(reference,baseline.target),'C5 exact clocked reference generation mismatch');end
  velocity=[0;diff(q)]/65536/.005;acceleration=[0;diff(velocity)]/.005;
  % Low-frequency inverse coefficients are derived from the frozen
  % representative plant plus the unchanged25/610 position loop.
  % This is model-based input compensation, not physical identification.
  for mode=[0 3]
   correction=zeros(size(q));
   if mode>=1,correction=5.35*velocity;end
   if mode>=2,correction=correction+2.9225625*acceleration;end
   if mode==3
    % A first-order input filter prevents high-frequency acceleration
    % quantization from being amplified. Its75ms low-frequency delay is
    % compensated in the analytic acceleration coefficient.
    raw=5.35*velocity+(2.9225625+5.35*.075)*acceleration;
    correction=zeros(size(raw));
    for n=2:numel(raw),correction(n)=correction(n-1)+(raw(n)-correction(n-1))/16;end
   end
   correction=round(correction);
   [x,motor,actual,valid]=simulate_reference(reference+correction,25,610);
   if tempo==1 && mode==0
    assert(isequal(motor,baseline.motor),'Nominal controller replay changed');
   end
   meta=data.word_metadata;lag=round(meta.window_ms/5)*tempo;
   selection=find(time>=.505+meta.word_start_ms/1000*tempo-1e-8 & time<.505+meta.word_end_ms/1000*tempo-1e-8);
   desired=[reference(selection),(reference(selection)-reference(selection-lag))*2*pi/1040/(lag*.005)];
   output=[actual(selection),(actual(selection)-actual(selection-lag))*2*pi/1040/(lag*.005)];
   span=meta.span_count;yspan=max(desired(:,2))-min(desired(:,2));
   rx=sqrt(mean((output(:,1)-desired(:,1)).^2));ry=sqrt(mean((output(:,2)-desired(:,2)).^2));
   cx=corrcoef(output(:,1),desired(:,1));cy=corrcoef(output(:,2),desired(:,2));actualSpan=max(output(:,1))-min(output(:,1));
   slot=slot+1;item=struct('word',word,'tempo',tempo,'compensation',mode,'valid',valid, ...
    'rms_position',rx,'actual_span',actualSpan,'rms_velocity_fraction',ry/yspan, ...
    'correlation_x',cx(1,2),'correlation_y',cy(1,2),'peak_angle',max(abs(x(:,1))), ...
    'peak_motor',max(abs(motor)),'peak_correction',max(abs(correction)), ...
    'numeric_pass',valid && max(abs(correction))<=256 && rx<=.05*span && actualSpan>=.85*span && actualSpan<=1.15*span && ry<=.1*yspan && cx(1,2)>=.95 && cy(1,2)>=.95);
   if slot==1,rows=item;else,rows(slot)=item;end
   if valid
    save(fullfile(root,sprintf('feedforward_%s_t%d_m%d.mat',word,tempo,mode)),'time','reference','q','velocity','acceleration','correction','x','motor','actual','selection','desired','output');
   end
  end
 end
end
tab=struct2table(rows);writetable(tab,'feedforward_filtered_screen.csv');disp(tab);
disp('ANALYSIS_ONLY: chosen mode still requires actual RTL and full safety validation.');diary off;
end
