"""Implements spectral biclustering algorithms.

Authors : Kemal Eren
License: BSD 3 clause

"""
from ..base import BaseEstimator, BiclusterMixin
from ..cluster.k_means_ import k_means

import numpy as np
from scipy.sparse.linalg import svds

def make_nonnegative(X, min_value=0):
    """Ensure `X.min()` >= `min_value`."""
    min_ = X.min()
    if min_ < min_value:
        X = X + (min_value - min_)
    return X


def scale_preprocess(X):
    """Normalize `X` by scaling rows and columns independently.

    Returns the normalized matrix and the row and column scaling
    factors.

    """
    X = make_nonnegative(X)
    row_diag = 1.0 / np.sqrt(np.sum(X, axis=1))
    col_diag = 1.0 / np.sqrt(np.sum(X, axis=0))
    an = row_diag[:, np.newaxis] * X * col_diag
    return an, row_diag, col_diag


def bistochastic_preprocess(X, maxiter=1000, tol=1e-5):
    """Normalize rows and columns of `X` simultaneously so that all
    rows sum to one constant and all columns sum to a different
    constant.

    """
    # According to paper, this can also be done more efficiently with
    # deviation reduction and balancing algorithms.
    X = make_nonnegative(X)
    X_scaled = X
    dist = None
    for i in range(maxiter):
        X_new, _, _ = scale_preprocess(X_scaled)
        dist = np.linalg.norm(X_scaled - X_new)
        X_scaled = X_new
        if dist is not None and dist < tol:
            break
    return X_scaled


def log_preprocess(X):
    """Normalize `X` according to Kluger's log-interactions scheme."""
    X = make_nonnegative(X, min_value=1)
    L = np.log(X)
    row_avg = np.mean(L, axis=1)[:, np.newaxis]
    col_avg = np.mean(L, axis=0)
    avg = np.mean(L)
    return L - row_avg - col_avg + avg


def fit_best_piecewise(vectors, k, n_clusters, random_state, n_init):
    """Find the `k` vectors that are best approximated by a piecewise
    constant vector. Returns them.

    The piecewise vectors are found by k-means; the best is chosen
    according to Euclidean distance.

    """
    # TODO: try all thresholds, as in paper
    def make_piecewise(v):
        centroid, labels, _ = k_means(v.reshape(-1, 1), n_clusters,
                                      random_state=random_state,
                                      n_init=n_init)
        return centroid[labels].reshape(-1)
    piecewise_vectors = np.apply_along_axis(make_piecewise, 1,
                                            vectors)
    dists = np.apply_along_axis(np.linalg.norm, 1,
                                vectors - piecewise_vectors)
    return vectors[np.argsort(dists)[:k]]


def project_and_cluster(data, vectors, n_clusters, random_state,
                        n_init):
    """Projects `data` to `vectors` and cluster the result."""
    projected = np.dot(data, vectors)
    _, labels, _ = k_means(projected, n_clusters,
                                  random_state=random_state,
                                  n_init=n_init)
    return labels


class SpectralBiclustering(BaseEstimator, BiclusterMixin):
    """Spectral biclustering.

    For equivalence with the Spectral Co-Clustering algorithm
    (Dhillon, 2001), use method='dhillon'.

    For the Spectral Biclustering algorithm (Kluger, 2003), use
    one of 'scale', 'bistochastic', or 'log'.

    Parameters
    -----------
    n_clusters : integer or tuple (rows, columns)
        The number of biclusters to find. If method is not 'dhillon',
        the number of row and column clusters may be different.

    method : string
        Method of normalizing and converting singular vectors into
        biclusters. May be one of 'dhillon', 'scale', 'bistochastic',
        or 'log'.

    n_singular_vectors : integer
        Number of singular vectors to check. Not used if
        `self.method` is 'dhillon'.

    n_best_vectors : integer
        Number of best singular vectors to which to project the data
        for clustering.

    maxiter : integer
        Maximum iterations for finding singular vectors.

    n_init : int, optional, default: 10
        Number of time the k-means algorithm will be run with different
        centroid seeds. The final results will be the best output of
        n_init consecutive runs in terms of inertia.

    random_state : int seed, RandomState instance, or None (default)
        A pseudo random number generator used by the K-Means
        initialization.

    Attributes
    ----------
    `rows_` : array-like, shape (n_row_clusters, n_rows)
        Results of the clustering. `rows[i, r]` is True if cluster `i`
        contains row `r`. Available only after calling ``fit``.

    `columns_` : array-like, shape (n_column_clusters, n_columns)
        Results of the clustering, like `rows`.

    `row_labels_` : array-like, shape (n_rows,)
        If `method` is not 'dhillon', the resulting biclusters have a
        checkerboard structure. The `row_labels_` and `column_labels_`
        attributes encode this checkerboard structure in a more
        compact form.

    `column_labels_` : array-like, shape (n_cols,)
        See `row_labels_`.


    References
    ----------

    - Co-clustering documents and words using
      bipartite spectral graph partitioning, 2001
      Dhillon, Inderjit S.
      http://citeseerx.ist.psu.edu/viewdoc/summary?doi=10.1.1.140.3011

    - Spectral biclustering of microarray data:
      coclustering genes and conditions, 2003
      Kluger, Yuval, et al.
      http://citeseerx.ist.psu.edu/viewdoc/summary?doi=10.1.1.135.1608

    """
    def __init__(self, n_clusters=3, method='bistochastic',
                 n_singular_vectors=6, n_best_vectors=3, maxiter=None,
                 n_init=10, random_state=None):
        if method not in ('dhillon', 'bistochastic', 'scale', 'log'):
            raise Exception('unknown method: {}'.format(method))
        if isinstance(n_clusters, tuple):
            if method == 'dhillon':
                raise Exception("different number of clusters not"
                                " supported when method=='dhillon'")
            if len(n_clusters) != 2:
                raise Exception("unsupported number of clusters")
        if n_best_vectors > n_singular_vectors:
            raise Exception('n_best_vectors > n_singular_vectors')
        self.n_clusters = n_clusters
        self.method = method
        self.n_singular_vectors = n_singular_vectors
        self.n_best_vectors = n_best_vectors
        self.maxiter = maxiter
        self.n_init = n_init
        self.random_state=random_state

    def _dhillon(self, X):
        normalized_data, row_diag, col_diag = scale_preprocess(X)
        n_singular_vals = 1 + int(np.ceil(np.log2(self.n_clusters)))
        u, s, vt = svds(normalized_data, k=n_singular_vals,
                        maxiter=self.maxiter)
        z = np.vstack((row_diag[:, np.newaxis] * u[:, 1:],
                       col_diag[:, np.newaxis] * vt.T[:, 1:]))
        _, labels, _ = k_means(z, self.n_clusters,
                               random_state=self.random_state,
                               n_init=self.n_init)

        n_rows = X.shape[0]
        row_labels = labels[0:n_rows]
        col_labels = labels[n_rows:]

        self.rows_ = np.vstack(row_labels == c for c in range(self.n_clusters))
        self.columns_ = np.vstack(col_labels == c for c in range(self.n_clusters))

    def _kluger(self, X):
        n_sv = self.n_singular_vectors
        if self.method == 'bistochastic':
            normalized_data = bistochastic_preprocess(X)
            n_sv += 1
        elif self.method == 'scale':
            normalized_data, _, _ = scale_preprocess(X)
            n_sv += 1
        elif self.method == 'log':
            normalized_data = log_preprocess(X)
        u, s, vt = svds(normalized_data, k=n_sv,
                        maxiter=self.maxiter)
        ut = u.T
        if self.method != 'log':
            ut = ut[1:]
            vt = vt[1:]

        if isinstance(self.n_clusters, tuple):
            n_row_clusters, n_col_clusters = self.n_clusters
        else:
            n_row_clusters = n_col_clusters = self.n_clusters

        best_ut = fit_best_piecewise(ut, self.n_best_vectors,
                                     n_row_clusters,
                                     self.random_state, self.n_init)

        best_vt = fit_best_piecewise(vt, self.n_best_vectors,
                                     n_col_clusters,
                                     self.random_state, self.n_init)


        row_vector = project_and_cluster(normalized_data, best_vt.T,
                                         n_row_clusters,
                                         self.random_state,
                                         self.n_init)

        col_vector = project_and_cluster(normalized_data.T, best_ut.T,
                                         n_col_clusters,
                                         self.random_state,
                                         self.n_init)


        _, row_labels = np.unique(row_vector, return_inverse=True)
        _, col_labels = np.unique(col_vector, return_inverse=True)

        rows = []
        cols = []

        for row_label in range(n_row_clusters):
            for col_label in range(n_col_clusters):
                rows.append(row_labels == row_label)
                cols.append(col_labels == col_label)

        self.rows_ = np.vstack(rows)
        self.columns_ = np.vstack(cols)

        self.row_labels_ = row_labels
        self.column_labels_ = col_labels


    def fit(self, X):
        """Creates a biclustering for X.

        Parameters
        ----------
        X : array-like, shape (n_samples, n_features)

        """
        if X.ndim != 2:
            raise Exception('data array must be 2 dimensional')
        if self.method == 'dhillon':
            self._dhillon(X)
        else:
            self._kluger(X)
