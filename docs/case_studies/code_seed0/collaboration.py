import numpy as np

def topk_cosine_sim(A: list[list[float]], Q: list[list[float]], k: int) -> list[list[int]]:
    # Convert to numpy arrays, using float32 for speed and memory efficiency
    # Cosine similarity ranking is robust to float32 precision
    A_np = np.array(A, dtype=np.float32)
    Q_np = np.array(Q, dtype=np.float32)
    
    # Compute squared norms for A and Q
    # Using einsum for potentially faster squared norm computation
    A_sq_norms = np.einsum('ij,ij->i', A_np, A_np)
    Q_sq_norms = np.einsum('ij,ij->i', Q_np, Q_np)
    
    # Compute norms, handling zero vectors by adding epsilon
    # np.maximum is efficient and vectorized
    A_norms = np.maximum(np.sqrt(A_sq_norms), 1e-10)
    Q_norms = np.maximum(np.sqrt(Q_sq_norms), 1e-10)
    
    # Normalize vectors
    # Reshape for broadcasting: (N, 1) and (M, 1)
    A_normalized = A_np / A_norms[:, np.newaxis]
    Q_normalized = Q_np / Q_norms[:, np.newaxis]
    
    # Compute cosine similarities using a single GEMM
    # Shape: (N, M)
    sims = np.dot(Q_normalized, A_normalized.T)
    
    N, M = sims.shape
    
    # Handle cases where k >= M
    if k >= M:
        # Sort all indices by similarity descending
        # argsort returns ascending, so we negate or use -sims
        top_indices = np.argsort(-sims, axis=1)[:, :k]
    else:
        # Use argpartition to efficiently find the top k elements
        # argpartition(sims, -k, axis=1) places the k-th largest element at index M-k
        # Elements from M-k to M-1 are the top k (unsorted)
        part_idx = np.argpartition(sims, -k, axis=1)[:, -k:]
        
        # Extract the similarity values for these top k candidates
        # Use take_along_axis to get values corresponding to part_idx
        top_k_sims = np.take_along_axis(sims, part_idx, axis=1)
        
        # Sort these top k candidates by similarity descending
        # argsort on the negated values gives ascending order of negated -> descending order of original
        sorted_indices_in_part = np.argsort(-top_k_sims, axis=1)
        
        # Gather the final indices
        top_indices = np.take_along_axis(part_idx, sorted_indices_in_part, axis=1)
    
    # Convert to list of lists
    results = [row.tolist() for row in top_indices]
    
    return results
