// Integer-only IEEE-754 conversion helpers. Round to nearest, ties to even.
// Control contract: finite operands, no overflow of C float->int conversion.
function [63:0] f32_to_f64;
 input [31:0] x;
 reg [23:0] m;
 integer e,k,j;
 begin
  if(x[30:0]==0) f32_to_f64={x[31],63'd0};
  else begin
   m={x[30:23]!=0,x[22:0]}; e=x[30:23];
   if(e==0) begin
    k=0; for(j=0;j<24;j=j+1) if(m[j]) k=j;
    m=m << (23-k); e=1-(23-k);
   end
   e=e-127+1023;
   f32_to_f64={x[31],11'd0,m[22:0],29'd0};
   f32_to_f64[62:52]=e;
  end
 end
endfunction

function [55:0] shift_jam56;
 input [55:0] v;
 input integer n;
 integer j;
 reg sticky;
 begin
  sticky=0;
  if(n<=0) shift_jam56=v;
  else if(n>=56) shift_jam56={55'd0,|v};
  else begin
   for(j=0;j<56;j=j+1) if(j<n) sticky=sticky|v[j];
   shift_jam56=(v >> n); shift_jam56[0]=shift_jam56[0]|sticky;
  end
 end
endfunction

function [31:0] f64_to_f32;
 input [63:0] x;
 reg [55:0] ext;
 reg [24:0] rounded;
 reg [23:0] keep;
 integer e;
 begin
  if(x[62:0]==0) f64_to_f32={x[63],31'd0};
  else begin
   e=x[62:52]-1023+127;
   // 53 significant bits; retain 24, then guard/round/sticky.
   ext={1'b1,x[51:0],3'b000};
   ext=shift_jam56(ext,29);
   if(e<=0) begin ext=shift_jam56(ext,1-e); e=0; end
   keep=ext[26:3];
   rounded={1'b0,keep}+(ext[2] && (ext[1] || ext[0] || keep[0]));
   if(rounded[24]) begin rounded=rounded>>1; e=e+1; end
   else if(e==0 && rounded[23]) e=1;
   if(e>=255) f64_to_f32={x[63],8'hff,23'd0};
   else begin f64_to_f32={x[63],8'd0,rounded[22:0]}; f64_to_f32[30:23]=e; end
  end
 end
endfunction

function [4:0] highest_bit32;
 input [31:0] mag;
 integer j;
 begin
  highest_bit32=0;
  for(j=0;j<32;j=j+1) if(mag[j]) highest_bit32=j;
 end
endfunction

// Exact RNE integer packing. At most eight low int32 bits are discarded.
function [31:0] int_mag_to_f32;
 input [31:0] mag;
 input [4:0] lead;
 input negative;
 reg [31:0] significand;
 reg [24:0] r;
 reg [7:0] exponent;
 reg guard_bit,sticky;
 reg [3:0] discard;
 integer j;
 begin
  exponent=8'd127+lead;guard_bit=0;sticky=0;discard=0;
  if(lead<=23) significand=mag<<(23-lead);
  else begin
   discard=lead-23;significand=mag>>discard;
   guard_bit=mag[discard-1'b1];
   for(j=0;j<7;j=j+1) if(j<discard-1) sticky=sticky|mag[j];
  end
  r={1'b0,significand[23:0]}+(guard_bit&&(sticky||significand[0]));
  if(r[24]) begin r=r>>1;exponent=exponent+1'b1;end
  int_mag_to_f32=(mag==0) ? 32'd0 : {negative,exponent,r[22:0]};
 end
endfunction

function [31:0] int_to_f32;
 input signed [31:0] x;
 reg [31:0] mag;
 begin
  mag=x[31] ? (~x+1'b1) : x;
  int_to_f32=int_mag_to_f32(mag,highest_bit32(mag),x[31]);
 end
endfunction

function signed [31:0] f32_to_int;
 input [31:0] x;
 reg [55:0] mag;
 integer e;
 begin
  e=x[30:23]-127;
  if(e<0) mag=0;
  else if(e>=23) mag={32'd0,1'b1,x[22:0]} << (e-23);
  else mag={32'd0,1'b1,x[22:0]} >> (23-e);
  f32_to_int=x[31] ? -$signed(mag[31:0]) : $signed(mag[31:0]);
 end
endfunction
