#include "mex.h"
#include <math.h>

int support_step(double *state, const double *parameters, double u, double dt);

/* Return mechanical states only. The caller samples ADC/ENC separately.
 * Duty inputs are physical PWM q15 / 32768; negate exactly once here.
 * Rows are t=0:10us:1ms; columns follow mechanical_ensemble.json. */
void mexFunction(int nlhs, mxArray *plhs[], int nrhs, const mxArray *prhs[]) {
    if (nrhs != 3 || nlhs != 1)
        mexErrMsgIdAndTxt("J280:Arguments", "Expected state6, parameters16, physicalDuty3 and one output.");
    const mwSize sizes[3] = {6, 16, 3};
    for (int a = 0; a < 3; ++a) {
        if (!mxIsDouble(prhs[a]) || mxIsComplex(prhs[a]) || mxIsSparse(prhs[a]) ||
            mxGetNumberOfElements(prhs[a]) != sizes[a])
            mexErrMsgIdAndTxt("J280:Shape", "Inputs must be full real double vectors of lengths6,16,3.");
        const double *v = mxGetPr(prhs[a]);
        for (mwSize k = 0; k < sizes[a]; ++k)
            if (!isfinite(v[k])) mexErrMsgIdAndTxt("J280:Finite", "Inputs must be finite.");
    }
    const double *p = mxGetPr(prhs[1]);
    const double *duty = mxGetPr(prhs[2]);
    if (!(p[2] > 0 && p[3] > 0 && p[15] > 0 && p[1] >= 0 && p[5] >= 0 &&
          p[6] >= 0 && p[7] >= 0 && p[12] >= 0 && p[13] >= 0))
        mexErrMsgIdAndTxt("J280:Parameters", "Invalid mass, dissipation or support parameters.");
    for (int k = 0; k < 3; ++k)
        if (fabs(duty[k]) > 1.0) mexErrMsgIdAndTxt("J280:Duty", "Physical normalized duty must lie in[-1,1].");
    double state[6];
    const double *initial = mxGetPr(prhs[0]);
    plhs[0] = mxCreateDoubleMatrix(101, 6, mxREAL);
    double *history = mxGetPr(plhs[0]);
    for (int k = 0; k < 6; ++k) {state[k] = initial[k]; history[101*k] = state[k];}
    for (int step = 0; step < 100; ++step) {
        const int segment = step < 10 ? 0 : (step < 20 ? 1 : 2);
        const int status = support_step(state, p, -duty[segment], 1e-5);
        if (status != 0) mexErrMsgIdAndTxt("J280:PlantStep", "Mechanical solver rejected step%d, status%d.", step, status);
        for (int k = 0; k < 6; ++k) {
            if (!isfinite(state[k])) mexErrMsgIdAndTxt("J280:PlantFinite", "Nonfinite mechanical state.");
            history[step+1+101*k] = state[k];
        }
    }
}
