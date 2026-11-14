"""Slow reference implementation of batch cosine similarity top-k."""
import math

def dot_product(v1, v2):
    s = 0.0
    for a, b in zip(v1, v2):
        s += a * b
    return s

def vector_norm(v):
    s = 0.0
    for a in v:
        s += a * a
    return math.sqrt(s)

def topk_cosine_sim(A, Q, k):
    results = []
    for q in Q:
        q_norm = vector_norm(q)
        sims = []
        for i, a in enumerate(A):
            a_norm = vector_norm(a)
            denom = q_norm * a_norm
            sim = dot_product(q, a) / (denom + 1e-12)
            sims.append((sim, i))
        for step in range(k):
            max_idx = step
            for j in range(step + 1, len(sims)):
                if sims[j][0] > sims[max_idx][0]:
                    max_idx = j
            sims[step], sims[max_idx] = sims[max_idx], sims[step]
        results.append([sims[j][1] for j in range(k)])
    return results
