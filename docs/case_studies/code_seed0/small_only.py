"""Optimized batch cosine similarity top-k using numpy."""
import numpy as np

def topk_cosine_sim(A: list[list[float]], Q: list[list[float]], k: int) -> list[list[int]]:
    A_np = np.array(A, dtype=np.float64)
    Q_np = np.array(Q, dtype=np.float64)
    
    M = A_np.shape[0]
    N = Q_np.shape[0]
    
    if M == 0 or N == 0:
        return [[] for _ in range(N)]
    
    effective_k = min(k, M)
    
    if effective_k == 0:
        return [[] for _ in range(N)]

    # Compute norms for A (M, D) -> (M, 1)
    A_norms = np.linalg.norm(A_np, axis=1, keepdims=True)
    # Avoid division by zero
    A_norms = np.where(A_norms == 0, 1.0, A_norms)
    A_normalized = A_np / A_norms
    
    # Compute norms for Q (N, D) -> (N, 1)
    Q_norms = np.linalg.norm(Q_np, axis=1, keepdims=True)
    Q_norms = np.where(Q_norms == 0, 1.0, Q_norms)
    Q_normalized = Q_np / Q_norms
    
    # Compute cosine similarity matrix: (N, M)
    sim_matrix = np.dot(Q_normalized, A_normalized.T)
    
    # Find top-k indices for each query
    results = []
    
    if effective_k == M:
        # If k >= M, we need all indices sorted by similarity descending
        # argsort ascending, then reverse
        indices = np.argsort(sim_matrix, axis=1)[:, ::-1]
        # Convert to list of lists
        for i in range(N):
            results.append(indices[i].tolist())
    else:
        # Use argpartition for O(N*M) complexity instead of O(N*M log M)
        # We want the largest k elements.
        # np.argpartition(arr, kth) puts the kth element in its sorted position,
        # with smaller elements to the left and larger to the right (or vice versa depending on implementation).
        # To get top-k largest, we can partition at M - k.
        # Elements at indices 0 to M-k-1 will be smaller or equal to the element at M-k.
        # Elements at indices M-k to M-1 will be larger or equal.
        # So the top-k largest are in the slice [M-k:].
        
        # However, argpartition does not guarantee order within the partitions.
        # We need to sort the top-k candidates to get the correct order.
        
        # Using argsort is simpler and often fast enough for moderate M.
        # For very large M and small k, argpartition is better.
        # Let's use argpartition for better worst-case performance.
        
        # Get the indices that would partition the array such that the top-k largest are at the end
        partition_indices = np.argpartition(sim_matrix, -effective_k, axis=1)
        
        # Extract the top-k candidates for each row
        # The top-k largest elements are in the last `effective_k` positions of the partitioned array
        top_k_indices = partition_indices[:, -effective_k:]
        
        # Get the values for these top-k candidates to sort them
        # We need to gather the values based on the partition indices
        # sim_matrix[i, top_k_indices[i]] gives the values
        
        # Create a mask to gather values efficiently
        # row_indices = np.arange(N).reshape(-1, 1)
        # top_k_values = sim_matrix[row_indices, top_k_indices]
        
        # Sort the top-k values within each row to get the final order
        # We need the indices that would sort the top_k_values for each row
        # Since top_k_indices contains the original indices in A, we need to map them back.
        
        # Let's create an array of the top-k values for sorting
        # top_k_values shape: (N, effective_k)
        top_k_values = np.take_along_axis(sim_matrix, top_k_indices, axis=1)
        
        # Get the indices that would sort top_k_values in descending order
        # argsort ascending, then reverse the last axis
        sort_indices = np.argsort(-top_k_values, axis=1)
        
        # Rearrange top_k_indices according to sort_indices
        final_indices = np.take_along_axis(top_k_indices, sort_indices, axis=1)
        
        # Convert to list of lists
        for i in range(N):
            results.append(final_indices[i].tolist())
            
    return results
