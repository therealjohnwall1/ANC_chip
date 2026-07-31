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

    R-> NxN
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
    
    p -> for lags K 0..N-1
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

def wiener_hopf(x_truth:np.ndarray, x_est:np.ndarray)->np.ndarray:
    """
    wiener hopf solution to minimize 
    J(w) = E[(d(n) - w^TX[n])^2]
    expand and take jacobian then set to 0 to get
    0 = -2p + 2Rw, solve, closed foorm for wiener hopf is  w
    """ 

    R = autocorrelation_matrix(x_est)
    p = crosscorrelation(x_est, x_truth)

    w = np.linalg.solve(R,p)
    
    return w

def lms_step(x:np.ndarray, error:np.ndarray, w_n:np.ndarray, lr:float)->np.ndarray:
    jac_hat = error * x
    w_n1 = w_n + lr * jac_hat

    return w_n1



def lms_walk(x_ref:np.ndarray, x_truth:np.ndarray, taps:int, lr:float)->list:
    """
    Gradient descent from least mean squared error using jacobian estimation
    Should converge to global min error on w_opt(taps) if space is convex

    steps through x_ref/x_truth sample by sample, updating w_n online via lms_step
    """
    assert(len(x_ref) == len(x_truth))

    n = len(x_ref)
    w_n = np.zeros(taps)
    history = [w_n.copy()]

    for i in range(taps, n):
        x_window = x_ref[i-taps:i][::-1]
        y_hat = w_n @ x_window
        error = x_truth[i] - y_hat
        w_n = lms_step(x_window, error, w_n, lr)
        history.append(w_n.copy())

    return history


