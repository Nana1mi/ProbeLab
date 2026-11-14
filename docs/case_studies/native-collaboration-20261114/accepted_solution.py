import math
import heapq

def topk_cosine_sim(A, Q, k):
    M = len(A)
    D = len(A[0]) if M else 0
    # Precompute norms of A once
    norms_A = [0.0] * M
    for i in range(M):
        a = A[i]
        s = 0.0
        for x in a:
            s += x * x
        norms_A[i] = math.sqrt(s)
    results = []
    for q in Q:
        qs = 0.0
        for x in q:
            qs += x * x
        q_norm = math.sqrt(qs)
        # Compute similarities, use heap for top-k
        heap = []
        for i in range(M):
            a = A[i]
            d = 0.0
            for j in range(D):
                d += q[j] * a[j]
            denom = q_norm * norms_A[i]
            sim = d / (denom + 1e-12)
            if len(heap) < k:
                heapq.heappush(heap, (sim, i))
            elif sim > heap[0][0]:
                heapq.heapreplace(heap, (sim, i))
        # Sort descending by similarity
        top = sorted(heap, key=lambda t: -t[0])
        results.append([idx for _, idx in top])
    return results