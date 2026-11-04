function prepare_word_data()
root=fileparts(mfilename('fullpath'));
fid=fopen(fullfile(root,'data','word_constants.vh'),'w');
cleanup=onCleanup(@()fclose(fid));
for word={'GOWIN','EasyStart'}
    name=word{1};data=jsondecode(fileread(fullfile(root,'data',['Word_' name '.json'])));
    p=double(data.points(:));n=numel(p);
    assert(n>=8 && n<=512 && all(p==fix(p)) && all(abs(p)<=3072));
    assert(all(p(1:4)==0) && all(p(end-3:end)==0));
    assert(all(abs(diff(p))<=96) && all(abs(diff([0;p]))<=96));
    assert(all(abs(diff([0;0;p],2))<=32) && sum(abs(diff(p)))<=16640);
    c=bitxor(uint32(hex2dec('6a09e667')),uint32(n));
    out=fopen(fullfile(root,'data',[name '.hex']),'w');
    for k=1:n
        fprintf(out,'%04X\n',mod(p(k),65536));
        value=bitor(bitshift(uint32(k-1),16),uint32(mod(p(k),65536)));
        c=bitxor(bitor(bitshift(c,1),bitshift(c,-31)),value);
    end
    fclose(out);
    assert(c==uint32(data.word_metadata.checksum),'Word checksum mismatch');
    fprintf(fid,'`define %s_POINTS %d\n',upper(name),n);
    fprintf(fid,'`define %s_CHECKSUM 32''h%s\n',upper(name),dec2hex(c,8));
    fprintf('%s: %d points, %.1fs, checksum %s\n',name,n,n*.1,dec2hex(c,8));
end
disp('WORD_INPUT_DATA_PASS');
end

