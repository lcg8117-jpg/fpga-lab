function regression_inputs()
root=fileparts(fileparts(mfilename('fullpath')));
for word={'GOWIN','EasyStart'}
 name=word{1};data=jsondecode(fileread(fullfile(root,'C5_Baseline','data',['Word_' name '.json'])));p=double(data.points(:));
 for mode=0:1
  folder=name;if mode,folder=['tracked_' name];end
  dest=fullfile(root,'Regression',folder);if ~isfolder(dest),mkdir(dest);end
  fid=fopen(fullfile(dest,'knots.hex'),'w');fprintf(fid,'%04X\n',mod(p,65536));fclose(fid);
  m=20*(1+mode);q=0;
  for s=1:numel(p)-3
   v=p(s:s+3);a=v(1)+4*v(2)+v(3);b=3*(v(3)-v(1));c=3*(v(1)-2*v(2)+v(3));d=-v(1)+3*v(2)-3*v(3)+v(4);
   j=(1:m)';q=[q;round((a*m^3+b*m^2*j+c*m*j.^2+d*j.^3)*65536/(6*m^3*16))];
  end
  fid=fopen(fullfile(dest,'oracle.hex'),'w');fprintf(fid,'%08X\n',mod(q,2^32));fclose(fid);
 end
end
copyfile(fullfile(root,'Regression','GOWIN','knots.hex'),fullfile(root,'Regression','guards','knots.hex'));
disp('REGRESSION_INPUTS_GENERATED_IN_MATLAB');
end
