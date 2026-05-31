#ifndef QUANT_CORE_H
#define QUANT_CORE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum {
    QC_OK = 0,
    QC_ERR_NULL = -1,
    QC_ERR_LENGTH = -2,
    QC_ERR_WINDOW = -3,
    QC_ERR_ALLOC = -4
};

int qc_rolling_mean_forward(const double *values, int64_t length, int window, double *out);
int qc_rolling_sum_forward(const double *values, int64_t length, int window, double *out);
int qc_rolling_min_forward(const double *values, int64_t length, int window, double *out);
int qc_rolling_max_forward(const double *values, int64_t length, int window, double *out);
int qc_count_forward(const int8_t *values, int64_t length, int window, double *out);
int qc_exist_forward(const int8_t *values, int64_t length, int window, int8_t *out);
int qc_ref_forward(const double *values, int64_t length, int periods, double *out);
int qc_ema_forward(const double *values, int64_t length, int span, double *out);
int qc_sma_tdx_forward(const double *values, int64_t length, int period, int weight, double *out);
int qc_kdj_ascending(
    const double *close,
    const double *low,
    const double *high,
    int64_t length,
    int period,
    int m1,
    int m2,
    double *k_out,
    double *d_out,
    double *j_out
);
int qc_zhixing_trend_forward(
    const double *close,
    int64_t length,
    int m1,
    int m2,
    int m3,
    int m4,
    double *short_out,
    double *bull_out
);
int qc_prepare_selection_features_forward(
    const double *open,
    const double *high,
    const double *low,
    const double *close,
    const double *volume,
    int64_t length,
    int include_trend,
    double *ref_close_out,
    double *ref_volume_out,
    int8_t *real_yang_out,
    int8_t *real_yin_out,
    double *k_out,
    double *d_out,
    double *j_out,
    double *short_out,
    double *bull_out
);

#ifdef __cplusplus
}
#endif

#endif
