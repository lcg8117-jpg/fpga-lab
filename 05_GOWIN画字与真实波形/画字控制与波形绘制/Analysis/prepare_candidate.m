function prepare_candidate()
root=fileparts(fileparts(mfilename('fullpath')));caseRoot=fullfile(root,'Candidate');
fid=fopen(fullfile(caseRoot,'data','word_constants.vh'),'w');cleanup=onCleanup(@()fclose(fid));
for word={'GOWIN','EasyStart'}
 name=word{1};data=jsondecode(fileread(fullfile(root,'C5_Baseline','data',['Word_' name '.json'])));
 p=double(data.points(:));n=numel(p);q=0;
 for s=1:n-3
  v=p(s:s+3);a=v(1)+4*v(2)+v(3);b=3*(v(3)-v(1));c=3*(v(1)-2*v(2)+v(3));d=-v(1)+3*v(2)-3*v(3)+v(4);
  j=(1:40)';q=[q;round((a*64000+b*1600*j+c*40*j.^2+d*j.^3)*65536/6144000)];
 end
 meta=data.word_metadata;
 for field={'total_s','pre_motion_s','word_s','window_ms','word_start_ms','word_end_ms'}
  meta.(field{1})=2*meta.(field{1});
 end
 meta.format='J280WordC6';meta.tracking_mode=1;meta.playback_scale=2;meta.playback_sample_ms=200;
 meta.pre_motion_wait_increased=true;meta.pid_modified=false;meta.output_smoothing=false;
 meta.input_interpolation='uniform cubic B-spline,40 actual5ms samples per200ms segment';
 meta.evidence='C6 candidate: model-based input lead, unchanged PID; actual RTL validation pending';
 checksum=bitxor(bitxor(uint32(hex2dec('6a09e667')),uint32(n)),uint32(1));
 out=fopen(fullfile(caseRoot,'data',[name '.hex']),'w');
 for k=1:n
  fprintf(out,'%04X\n',mod(p(k),65536));value=bitor(bitshift(uint32(k-1),16),uint32(mod(p(k),65536)));
  checksum=bitxor(bitor(bitshift(checksum,1),bitshift(checksum,-31)),value);
 end
 fclose(out);meta.checksum=double(checksum);data.word_metadata=meta;
 indices=(meta.word_start_ms/5:meta.word_end_ms/5-1)'+1;lag=meta.window_ms/5;
 data.target_only_xy=[q(indices)/65536,(q(indices)-q(indices-lag))/65536*2*pi/1040/(meta.window_ms/1000)];
 data.tracking_mode=1;
 target=fullfile(caseRoot,'data',['Word_' name '.json']);f=fopen(target,'w');fprintf(f,'%s',jsonencode(data));fclose(f);
 copyfile(target,fullfile(root,'Source','examples',['Word_' name '.json']));
 fprintf(fid,'`define %s_POINTS %d\n',upper(name),n);
 fprintf(fid,'`define %s_CHECKSUM 32''h%s\n',upper(name),dec2hex(checksum,8));
 fprintf('%s mode1 %dknots total=%.1fs window=%.1fs checksum=%s\n',name,n,meta.total_s,meta.window_ms/1000,dec2hex(checksum,8));
end
disp('C6_CANDIDATE_INPUTS_READY');
end
