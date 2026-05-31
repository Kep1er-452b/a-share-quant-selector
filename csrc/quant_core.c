#include "quant_core.h"

#include <math.h>
#include <stdlib.h>

static int validate_double_io(const double *values, int64_t length, int window, double *out) {
    if (values == NULL || out == NULL) {
        return QC_ERR_NULL;
    }
    if (length < 0) {
        return QC_ERR_LENGTH;
    }
    if (window <= 0) {
        return QC_ERR_WINDOW;
    }
    return QC_OK;
}

int qc_rolling_mean_forward(const double *values, int64_t length, int window, double *out) {
    int status = validate_double_io(values, length, window, out);
    if (status != QC_OK) {
        return status;
    }
    double sum = 0.0;
    int64_t count = 0;
    for (int64_t i = length; i-- > 0;) {
        if (!isnan(values[i])) {
            sum += values[i];
            count++;
        }
        int64_t stale = i + (int64_t)window;
        if (stale < length && !isnan(values[stale])) {
            sum -= values[stale];
            count--;
        }
        out[i] = count > 0 ? sum / (double)count : NAN;
    }
    return QC_OK;
}

int qc_rolling_sum_forward(const double *values, int64_t length, int window, double *out) {
    int status = validate_double_io(values, length, window, out);
    if (status != QC_OK) {
        return status;
    }
    double sum = 0.0;
    int64_t count = 0;
    for (int64_t i = length; i-- > 0;) {
        if (!isnan(values[i])) {
            sum += values[i];
            count++;
        }
        int64_t stale = i + (int64_t)window;
        if (stale < length && !isnan(values[stale])) {
            sum -= values[stale];
            count--;
        }
        out[i] = count > 0 ? sum : NAN;
    }
    return QC_OK;
}

int qc_rolling_min_forward(const double *values, int64_t length, int window, double *out) {
    int status = validate_double_io(values, length, window, out);
    if (status != QC_OK) {
        return status;
    }
    if (length == 0) {
        return QC_OK;
    }

    int64_t *deque = (int64_t *)malloc((size_t)length * sizeof(int64_t));
    if (deque == NULL) {
        return QC_ERR_ALLOC;
    }

    int64_t head = 0;
    int64_t tail = 0;
    for (int64_t i = length; i-- > 0;) {
        int64_t stale_start = i + (int64_t)window;
        while (head < tail && deque[head] >= stale_start) {
            head++;
        }
        if (!isnan(values[i])) {
            while (head < tail && values[deque[tail - 1]] >= values[i]) {
                tail--;
            }
            deque[tail++] = i;
        }
        out[i] = head < tail ? values[deque[head]] : NAN;
    }

    free(deque);
    return QC_OK;
}

int qc_rolling_max_forward(const double *values, int64_t length, int window, double *out) {
    int status = validate_double_io(values, length, window, out);
    if (status != QC_OK) {
        return status;
    }
    if (length == 0) {
        return QC_OK;
    }

    int64_t *deque = (int64_t *)malloc((size_t)length * sizeof(int64_t));
    if (deque == NULL) {
        return QC_ERR_ALLOC;
    }

    int64_t head = 0;
    int64_t tail = 0;
    for (int64_t i = length; i-- > 0;) {
        int64_t stale_start = i + (int64_t)window;
        while (head < tail && deque[head] >= stale_start) {
            head++;
        }
        if (!isnan(values[i])) {
            while (head < tail && values[deque[tail - 1]] <= values[i]) {
                tail--;
            }
            deque[tail++] = i;
        }
        out[i] = head < tail ? values[deque[head]] : NAN;
    }

    free(deque);
    return QC_OK;
}

int qc_count_forward(const int8_t *values, int64_t length, int window, double *out) {
    if (values == NULL || out == NULL) {
        return QC_ERR_NULL;
    }
    if (length < 0) {
        return QC_ERR_LENGTH;
    }
    if (window <= 0) {
        return QC_ERR_WINDOW;
    }
    int64_t count = 0;
    for (int64_t i = length; i-- > 0;) {
        if (values[i] != 0) {
            count++;
        }
        int64_t stale = i + (int64_t)window;
        if (stale < length && values[stale] != 0) {
            count--;
        }
        out[i] = (double)count;
    }
    return QC_OK;
}

int qc_exist_forward(const int8_t *values, int64_t length, int window, int8_t *out) {
    if (values == NULL || out == NULL) {
        return QC_ERR_NULL;
    }
    if (length < 0) {
        return QC_ERR_LENGTH;
    }
    if (window <= 0) {
        return QC_ERR_WINDOW;
    }
    int64_t count = 0;
    for (int64_t i = length; i-- > 0;) {
        if (values[i] != 0) {
            count++;
        }
        int64_t stale = i + (int64_t)window;
        if (stale < length && values[stale] != 0) {
            count--;
        }
        out[i] = (int8_t)(count > 0);
    }
    return QC_OK;
}

int qc_ref_forward(const double *values, int64_t length, int periods, double *out) {
    int status = validate_double_io(values, length, periods > 0 ? periods : 1, out);
    if (status != QC_OK) {
        return status;
    }
    if (periods < 0) {
        return QC_ERR_WINDOW;
    }
    for (int64_t i = 0; i < length; i++) {
        int64_t source = i + periods;
        out[i] = source < length ? values[source] : NAN;
    }
    return QC_OK;
}

int qc_ema_forward(const double *values, int64_t length, int span, double *out) {
    int status = validate_double_io(values, length, span, out);
    if (status != QC_OK) {
        return status;
    }
    if (length == 0) {
        return QC_OK;
    }
    double alpha = 2.0 / ((double)span + 1.0);
    out[length - 1] = values[length - 1];
    for (int64_t i = length - 2; i >= 0; i--) {
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i + 1];
    }
    return QC_OK;
}

int qc_sma_tdx_forward(const double *values, int64_t length, int period, int weight, double *out) {
    int status = validate_double_io(values, length, period, out);
    if (status != QC_OK) {
        return status;
    }
    if (length == 0) {
        return QC_OK;
    }
    out[length - 1] = values[length - 1];
    for (int64_t i = length - 2; i >= 0; i--) {
        out[i] = (values[i] * (double)weight + out[i + 1] * (double)(period - weight)) / (double)period;
    }
    return QC_OK;
}

static int rolling_min_ascending(const double *values, int64_t length, int window, double *out) {
    if (length == 0) {
        return QC_OK;
    }

    int64_t *deque = (int64_t *)malloc((size_t)length * sizeof(int64_t));
    if (deque == NULL) {
        return QC_ERR_ALLOC;
    }

    int64_t head = 0;
    int64_t tail = 0;
    for (int64_t i = 0; i < length; i++) {
        int64_t start = i - window + 1;
        if (start < 0) {
            start = 0;
        }
        while (head < tail && deque[head] < start) {
            head++;
        }
        if (!isnan(values[i])) {
            while (head < tail && values[deque[tail - 1]] >= values[i]) {
                tail--;
            }
            deque[tail++] = i;
        }
        out[i] = head < tail ? values[deque[head]] : NAN;
    }

    free(deque);
    return QC_OK;
}

static int rolling_max_ascending(const double *values, int64_t length, int window, double *out) {
    if (length == 0) {
        return QC_OK;
    }

    int64_t *deque = (int64_t *)malloc((size_t)length * sizeof(int64_t));
    if (deque == NULL) {
        return QC_ERR_ALLOC;
    }

    int64_t head = 0;
    int64_t tail = 0;
    for (int64_t i = 0; i < length; i++) {
        int64_t start = i - window + 1;
        if (start < 0) {
            start = 0;
        }
        while (head < tail && deque[head] < start) {
            head++;
        }
        if (!isnan(values[i])) {
            while (head < tail && values[deque[tail - 1]] <= values[i]) {
                tail--;
            }
            deque[tail++] = i;
        }
        out[i] = head < tail ? values[deque[head]] : NAN;
    }

    free(deque);
    return QC_OK;
}

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
) {
    if (close == NULL || low == NULL || high == NULL || k_out == NULL || d_out == NULL || j_out == NULL) {
        return QC_ERR_NULL;
    }
    if (length < 0) {
        return QC_ERR_LENGTH;
    }
    if (period <= 0 || m1 <= 0 || m2 <= 0) {
        return QC_ERR_WINDOW;
    }
    if (length == 0) {
        return QC_OK;
    }

    double *low_min = (double *)malloc((size_t)length * sizeof(double));
    double *high_max = (double *)malloc((size_t)length * sizeof(double));
    if (low_min == NULL || high_max == NULL) {
        free(low_min);
        free(high_max);
        return QC_ERR_ALLOC;
    }

    int status = rolling_min_ascending(low, length, period, low_min);
    if (status != QC_OK) {
        free(low_min);
        free(high_max);
        return status;
    }
    status = rolling_max_ascending(high, length, period, high_max);
    if (status != QC_OK) {
        free(low_min);
        free(high_max);
        return status;
    }

    k_out[0] = 50.0;
    d_out[0] = 50.0;
    j_out[0] = 50.0;
    for (int64_t i = 1; i < length; i++) {
        double range = high_max[i] - low_min[i];
        double rsv = 50.0;
        if (i >= (int64_t)period - 1 && range != 0.0 && !isnan(range)) {
            rsv = (close[i] - low_min[i]) / range * 100.0;
        }
        k_out[i] = (rsv + k_out[i - 1] * (double)(m1 - 1)) / (double)m1;
        d_out[i] = (k_out[i] + d_out[i - 1] * (double)(m2 - 1)) / (double)m2;
        j_out[i] = 3.0 * k_out[i] - 2.0 * d_out[i];
    }

    free(low_min);
    free(high_max);
    return QC_OK;
}

int qc_zhixing_trend_forward(
    const double *close,
    int64_t length,
    int m1,
    int m2,
    int m3,
    int m4,
    double *short_out,
    double *bull_out
) {
    if (close == NULL || short_out == NULL || bull_out == NULL) {
        return QC_ERR_NULL;
    }
    if (length < 0) {
        return QC_ERR_LENGTH;
    }
    if (m1 <= 0 || m2 <= 0 || m3 <= 0 || m4 <= 0) {
        return QC_ERR_WINDOW;
    }
    if (length == 0) {
        return QC_OK;
    }

    double *ema_once = (double *)malloc((size_t)length * sizeof(double));
    double *ma1 = (double *)malloc((size_t)length * sizeof(double));
    double *ma2 = (double *)malloc((size_t)length * sizeof(double));
    double *ma3 = (double *)malloc((size_t)length * sizeof(double));
    double *ma4 = (double *)malloc((size_t)length * sizeof(double));
    if (ema_once == NULL || ma1 == NULL || ma2 == NULL || ma3 == NULL || ma4 == NULL) {
        free(ema_once);
        free(ma1);
        free(ma2);
        free(ma3);
        free(ma4);
        return QC_ERR_ALLOC;
    }

    qc_ema_forward(close, length, 10, ema_once);
    qc_ema_forward(ema_once, length, 10, short_out);
    qc_rolling_mean_forward(close, length, m1, ma1);
    qc_rolling_mean_forward(close, length, m2, ma2);
    qc_rolling_mean_forward(close, length, m3, ma3);
    qc_rolling_mean_forward(close, length, m4, ma4);

    for (int64_t i = 0; i < length; i++) {
        bull_out[i] = (ma1[i] + ma2[i] + ma3[i] + ma4[i]) / 4.0;
    }

    free(ema_once);
    free(ma1);
    free(ma2);
    free(ma3);
    free(ma4);
    return QC_OK;
}

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
) {
    if (
        open == NULL || high == NULL || low == NULL || close == NULL || volume == NULL ||
        ref_close_out == NULL || ref_volume_out == NULL || real_yang_out == NULL || real_yin_out == NULL ||
        k_out == NULL || d_out == NULL || j_out == NULL
    ) {
        return QC_ERR_NULL;
    }
    if (length < 0) {
        return QC_ERR_LENGTH;
    }
    if (include_trend && (short_out == NULL || bull_out == NULL)) {
        return QC_ERR_NULL;
    }
    if (length == 0) {
        return QC_OK;
    }

    int status = qc_ref_forward(close, length, 1, ref_close_out);
    if (status != QC_OK) {
        return status;
    }
    status = qc_ref_forward(volume, length, 1, ref_volume_out);
    if (status != QC_OK) {
        return status;
    }

    for (int64_t i = 0; i < length; i++) {
        int close_gt_open = close[i] > open[i];
        int close_lt_open = close[i] < open[i];
        int close_lt_ref = close[i] < ref_close_out[i];
        int close_gt_ref = close[i] > ref_close_out[i];
        real_yang_out[i] = (int8_t)(close_gt_open && !close_lt_ref);
        real_yin_out[i] = (int8_t)(close_lt_open && !close_gt_ref);
    }

    double *close_asc = (double *)malloc((size_t)length * sizeof(double));
    double *low_asc = (double *)malloc((size_t)length * sizeof(double));
    double *high_asc = (double *)malloc((size_t)length * sizeof(double));
    double *k_asc = (double *)malloc((size_t)length * sizeof(double));
    double *d_asc = (double *)malloc((size_t)length * sizeof(double));
    double *j_asc = (double *)malloc((size_t)length * sizeof(double));
    if (close_asc == NULL || low_asc == NULL || high_asc == NULL || k_asc == NULL || d_asc == NULL || j_asc == NULL) {
        free(close_asc);
        free(low_asc);
        free(high_asc);
        free(k_asc);
        free(d_asc);
        free(j_asc);
        return QC_ERR_ALLOC;
    }

    for (int64_t i = 0; i < length; i++) {
        int64_t source = length - 1 - i;
        close_asc[i] = close[source];
        low_asc[i] = low[source];
        high_asc[i] = high[source];
    }

    status = qc_kdj_ascending(close_asc, low_asc, high_asc, length, 9, 3, 3, k_asc, d_asc, j_asc);
    if (status == QC_OK) {
        for (int64_t i = 0; i < length; i++) {
            int64_t target = length - 1 - i;
            k_out[target] = k_asc[i];
            d_out[target] = d_asc[i];
            j_out[target] = j_asc[i];
        }
    }

    free(close_asc);
    free(low_asc);
    free(high_asc);
    free(k_asc);
    free(d_asc);
    free(j_asc);
    if (status != QC_OK) {
        return status;
    }

    if (include_trend) {
        status = qc_zhixing_trend_forward(close, length, 14, 28, 57, 114, short_out, bull_out);
        if (status != QC_OK) {
            return status;
        }
    }
    return QC_OK;
}
