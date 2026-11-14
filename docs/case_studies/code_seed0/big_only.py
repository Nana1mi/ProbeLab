import numpy as np

def topk_cosine_sim(A, Q, k):
    A_arr = np.asarray(A, dtype=np.float64)
    Q_arr = np.asarray(Q, dtype=np.float64)
    N = Q_arr.shape[0]
    M = A_arr.shape[0]
    if k <= 0 or M == 0 or N == 0:
        return [[] for _ in range(N)]
    kk = min(k, M)

    # Normalize database and queries to unit length; cosine = dot product of unit vectors.
    a_norms = np.linalg.norm(A_arr, axis=1)
    q_norms = np.linalg.norm(Q_arr, axis=1)
    a_norms[a_norms == 0] = 1.0
    q_norms[q_norms == 0] = 1.0
    A_n = A_arr / a_norms[:, None]
    Q_n = Q_arr / q_norms[:, None]

    sims = Q_n @ A_n.T  # N x M

    if kk < M:
        # Partition in place along last axis to isolate top-kk candidates.
        idx = np.argpartition(-sims, kk - 1, axis=1)[:, :kk]
    else:
        idx = np.broadcast_to(np.arange(M), (N, M)).copy()

    # Sort the selected candidates descending by similarity.
    sel = np.take_along_axis(sims, idx, axis=1)
    order = np.argsort(-sel, axis=1, kind='stable')
    idx = np.take_along_axis(idx, order, axis=1)

    return idx.tolist()
