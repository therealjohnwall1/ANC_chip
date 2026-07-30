import numpy as np

def autocorrelation(x:np.ndarray, lag:int)->float:
    """
    calculate the autocorrelation of signal x with lag 
    normalized result returned
    """
    n = len(x)
    total = 0
    mean = sum(x)/n
    x_dec = x - mean

    for i in range(n-lag):
        total += x_dec[i] * x_dec[i+lag]

    density = 0
    for i in range(n):
        density += x_dec[i] * x_dec[i]

    return total/density


def autocorrelation_matrix(x:np.ndarray)->np.ndarray:
    """
    build the Toeplitz autocorrelation matrix of signal x
    R[i,j] = r(|i-j|), using autocorrelation() for each lag
    """
    n = len(x)
    r = np.array([autocorrelation(x, lag) for lag in range(n)])

    R = np.empty((n, n))
    for i in range(n):
        for j in range(n):
            R[i, j] = r[abs(i - j)]

    return R


def crosscorrelation(x:np.ndarray, y:np.ndarray)->np.ndarray:
    """
    calculate the crosscorrelation vector between signals x and y
    p[k] = normalized sum_n x[n] * y[n+k], for lag k = 0..n-1
    """
    n = len(x)
    x_dec = x - sum(x) / n
    y_dec = y - sum(y) / n

    assert(len(x) == len(y))

    density_x = 0
    density_y = 0
    for i in range(n):
        density_x += x_dec[i] * x_dec[i]
        density_y += y_dec[i] * y_dec[i]
    density = np.sqrt(density_x * density_y)

    p = np.empty(n)
    for lag in range(n):
        total = 0
        for i in range(n - lag):
            total += x_dec[i] * y_dec[i + lag]
        p[lag] = total / density

    return p

