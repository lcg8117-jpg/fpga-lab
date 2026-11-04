function regression_font()
root=fileparts(fileparts(mfilename('fullpath')));
folder=fullfile(root,'Regression','tracked_EasyStart_Font3');if ~isfolder(folder),mkdir(folder);end
data=jsondecode(fileread(fullfile(root,'Source','host','easy_start_plan.json')));p=double(data.points(:));
f=fopen(fullfile(folder,'knots.hex'),'w');fprintf(f,'%04X\n',mod(p,65536));fclose(f);
q=0;j=(1:40)';
for s=1:numel(p)-3
 v=p(s:s+3);a=v(1)+4*v(2)+v(3);b=3*(v(3)-v(1));c=3*(v(1)-2*v(2)+v(3));d=-v(1)+3*v(2)-3*v(3)+v(4);
 q=[q;round((a*64000+b*1600*j+c*40*j.^2+d*j.^3)*65536/6144000)];
end
f=fopen(fullfile(folder,'oracle.hex'),'w');fprintf(f,'%08X\n',mod(q,2^32));fclose(f);
fprintf('FONT3_REFERENCE_ORACLE_READY knots=%d samples=%d\n',numel(p),numel(q));
end
