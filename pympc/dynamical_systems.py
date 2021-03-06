import numpy as np
import scipy.linalg as linalg
import matplotlib.pyplot as plt
from optimization.gurobi import linear_program
from geometry.polytope import Polytope
from algebra import rangespace_basis, nullspace_basis
import h5py


### CLASSES OF DYNAMICAL SYSTEMS

class LinearSystem:
    """
    Discrete time linear systems in the form:
    x_{k+1} = A x_k + B u_k.

    VARIABLES:
        A: discrete time state transition matrix
        B: discrete time input to state map
        n_x: number of sates
        n_u: number of inputs
    """

    def __init__(self, A, B):
        self.A = A
        self.B = B
        self.n_x, self.n_u = np.shape(B)
        return

    def condense(self, N):
        """
        Generates a fake PWA system and calls condense_dynamical_system().
        """
        c = np.zeros((self.n_x, 1))
        sys = AffineSystem(self.A, self.B, c)
        affine_systems = [sys]
        switching_sequence = [0]*N
        return condense_dynamical_system(affine_systems, switching_sequence)[0:2]

    def simulate(self, x0, u_list):
        N = len(u_list)
        A_bar, B_bar = self.condense(N)
        c_bar = np.zeros((self.n_x*(N+1), 1))
        return simulate_affine_dynamics(A_bar, B_bar, c_bar, x0, u_list)

    @staticmethod
    def from_continuous(A, B, h, method='zoh'):
        c = np.zeros((A.shape[0], 1))
        if method == 'zoh':
            A_d, B_d, _ = zero_order_hold(A, B, c, h)
        elif method == 'explicit_euler':
            A_d, B_d, _ = explicit_euler(A, B, c, h)
        return LinearSystem(A_d, B_d)

class AffineSystem:
    """
    Discrete time affine systems in the form:
    x_{k+1} = A x_k + B u_k + c.

    VARIABLES:
        A: discrete time state transition matrix
        B: discrete time input to state map
        c: discrete time offset term
        n_x: number of sates
        n_u: number of inputs
    """

    def __init__(self, A, B, c):
        self.A = A
        self.B = B
        self.c = c
        self.n_x, self.n_u = np.shape(B)

    def condense(self, N):
        """
        Generates a fake PWA system and calls condense_dynamical_system().
        """
        affine_systems = [self]
        switching_sequence = [0]*N
        return condense_dynamical_system(affine_systems, switching_sequence)

    def simulate(self, x0, u_list):
        N = len(u_list)
        A_bar, B_bar, c_bar = self.condense(N)
        return simulate_affine_dynamics(A_bar, B_bar, c_bar, x0, u_list)

    def save(self, group_name, super_group=None):
        """
        Saves the matrices A, B, c in the group_name.hdf5 file.
        If a super group is provided, instead of saving the file, it adds the sub group gorup_name to the super group.
        """

        # open the file
        if super_group is None:
            group = h5py.File(group_name + '.hdf5', 'w')
        else:
            group = super_group.create_group(group_name)

        # write the matrices
        A = group.create_dataset('A', (self.n_x, self.n_x))
        B = group.create_dataset('B', (self.n_x, self.n_u))
        c = group.create_dataset('c', (self.n_x, 1))
        A[...] = self.A
        B[...] = self.B
        c[...] = self.c

        # close the file and return
        if super_group is None:
            group.close()
            return
        else:
            return super_group

    @staticmethod
    def from_continuous(A, B, c, h, method='zoh'):
        if len(c.shape) == 1:
            c = np.reshape(c, (c.shape[0], 1))
        if method == 'zoh':
            A_d, B_d, c_d = zero_order_hold(A, B, c, h)
        elif method == 'explicit_euler':
            A_d, B_d, c_d = explicit_euler(A, B, c, h)
        elif method == 'semi_implicit_euler':
            n = A.shape[0]/2
            A_q = A[n:, :n]
            A_v = A[n:, n:]
            B_v = B[n:, :]
            c_v = c[n:, :]
            A_d, B_d, c_d = semi_implicit_euler(A_q, A_v, B_v, c_v, h)
        else:
            raise ValueError('Unknown discretization method.')
        return AffineSystem(A_d, B_d, c_d)

def upload_AffineSystem(group_name, super_group=None):
    """
    Reads the file group_name.hdf5 and generates an affine system from the data therein.
    If a super_group is provided, reads the sub group named group_name which belongs to the super_group.
    """

    # open the file
    if super_group is None:
        affine_system = h5py.File(group_name + '.hdf5', 'r')
    else:
        affine_system = super_group[group_name]

    # read the matrices
    A = np.array(affine_system['A'])
    B = np.array(affine_system['B'])
    c = np.array(affine_system['c'])

    # close the file and return    
    if super_group is None:
        affine_system.close()
    return AffineSystem(A, B, c)

class PieceWiseAffineSystem(object):
    """
    Discrete time piecewise affine systems in the form:
    x_{k+1} = A_i x_k + B_i u_k + c_i   if   (x_k, u_k) \in D_i

    VARIABLES:
        affine_systems: list of affine systems
        domains: list of state AND input domains D_i (D_i are polytopes, see the Polytope class in geometry.polytope)
        n_x: number of sates
        n_u: number of inputs
        n_sys: number of affine subsystems
        x_min, x_max: minimum and maximum (elementwise) values of x (state), for x in the union of the domains D_i (useful for sampling in the state space)
    """

    def __init__(self, affine_systems, domains):
        self.affine_systems = affine_systems
        self.domains = domains
        self.n_x = affine_systems[0].n_x
        self.n_u = affine_systems[0].n_u
        self.n_sys = len(affine_systems)
        self._x_min = None
        self._x_max = None
        return

    def condense(self, switching_sequence):
        """
        See condense_dynamical_system().
        """
        return condense_dynamical_system(self.affine_systems, switching_sequence)

    def simulate(self, x0, u_list):
        """
        Given the initial state x0 and a list of inputs, simulates the PWA dynamics and returns: i) the state trajectory as a list of state vectors, ii) the mode sequence. If the couple (x_k, u_k) goes out of the domains D_i raises a ValueError.
        """
        N = len(u_list)
        x_list = [x0]
        switching_sequence = []
        for k in range(N):
            domain = self.find_domain(x_list[k], u_list[k])
            if domain is None:
                raise ValueError('Unfeasible state x = ' + str(x_list[k].flatten()) + ' or input u = ' + str(u_list[k].flatten()))
            else:
                sys = self.affine_systems[domain]
                x_list.append(sys.A.dot(x_list[k]) + sys.B.dot(u_list[k]) + sys.c)
                switching_sequence.append(domain)
        return x_list, switching_sequence

    def find_domain(self, x, u):
        """
        Given (x,u) returns the i such that (x,u) \in D_i.
        """
        for i in range(self.n_sys):
            if self.domains[i].applies_to(np.vstack((x, u))):
                return i
        return None

    def save(self, group_name, super_group=None):
        """
        Saves the lists affine_systems anf domains in the group_name.hdf5 file.
        If a super group is provided, instead of saving the file, it adds the sub group gorup_name to the super group.
        """

        # open the file
        if super_group is None:
            group = h5py.File(group_name + '.hdf5', 'w')
        else:
            group = super_group.create_group(group_name)

        # write affine systems
        affine_systems = group.create_group('affine_systems')
        for i, affine_system in enumerate(self.affine_systems):
            affine_systems = affine_system.save(str(i), affine_systems)

        # write domains # maybe do a save method in polytope?
        domains = group.create_group('domains')
        for i, D_i in enumerate(self.domains):
            domain = domains.create_group(str(i))
            A = domain.create_dataset('A', D_i.lhs_min.shape)
            b = domain.create_dataset('b', D_i.rhs_min.shape)
            A[...] = D_i.lhs_min
            b[...] = D_i.rhs_min

        # close the file and return
        if super_group is None:
            group.close()
            return
        else:
            return super_group
        
    @property
    def x_min(self):
        if self._x_min is None:
            min_list = [domain.x_min[0:self.n_x,:] for domain in self.domains]
            self._x_min = np.array([[min([x[i,0] for x in min_list])] for i in range(self.n_x)])
        return self._x_min

    @property
    def x_max(self):
        if self._x_max is None:
            max_list = [domain.x_max[0:self.n_x,:] for domain in self.domains]
            self._x_max = np.array([[max([x[i,0] for x in max_list])] for i in range(self.n_x)])
        return self._x_max

    @staticmethod
    def from_orthogonal_domains(affine_systems, state_domains, input_domains):
        """
        Allows to construct a PWA system when the domain of the state and the input are orthogonal, i.e.: D_i = X_i \times U_i. It requires the list of domains for state and input: state_domains = [X_0, ... , X_s], input_domains = [U_0, ... , U_s]
        """
        domains = []
        for i in range(len(state_domains)):
            A_i = linalg.block_diag(state_domains[i].lhs_min, input_domains[i].lhs_min)
            b_i = np.vstack((state_domains[i].rhs_min, input_domains[i].rhs_min))
            domain_i = Polytope(A_i, b_i)
            domain_i.assemble()
            domains.append(domain_i)
        return PieceWiseAffineSystem(affine_systems, domains)

def upload_PieceWiseAffineSystem(group_name, super_group=None):
    """
    Reads the file group_name.hdf5 and generates a piecewise affine system from the data therein.
    If a super_group is provided, reads the sub group named group_name which belongs to the super_group.
    """

    # open the file
    if super_group is None:
        piecewise_affine_system = h5py.File(group_name + '.hdf5', 'r')
    else:
        piecewise_affine_system = super_group[group_name]

    # read affine systems
    n = len(piecewise_affine_system['affine_systems'])
    affine_systems = []
    for i in range(n):
        affine_systems.append(upload_AffineSystem(str(i), piecewise_affine_system['affine_systems']))
    
    # read domains
    domains = []
    for i in range(n):
        domain = piecewise_affine_system['domains'][str(i)]
        p = Polytope(np.array(domain['A']), np.array(domain['b']))
        p.assemble()
        domains.append(p)

    # close the file and return
    if super_group is None:
        piecewise_affine_system.close()
    return PieceWiseAffineSystem(affine_systems, domains)


### AUXILIARY FUNCTIONS

def productory(matrix_list):
    prod = matrix_list[0]
    for i in range(1, len(matrix_list)):
        prod = prod.dot(matrix_list[i])
    return prod

def simulate_affine_dynamics(A_bar, B_bar, c_bar, x0, u_list):

    # reshape initial state (from vector to matrix)
    n_x = x0.shape[0]
    if x0.ndim == 1:
        x0 = np.reshape(x0, (n_x, 1))

    # derive state trajectory including initial state
    x_vec = A_bar.dot(x0) + B_bar.dot(np.vstack(u_list)) + c_bar
    N = len(u_list)
    x_list = []
    [x_list.append(x_vec[n_x*i:n_x*(i+1)]) for i in range(N+1)]

    return x_list

def condense_dynamical_system(affine_systems, switching_sequence):
    """
    For the PWA system
    x(t+1) = A_i x(t) + B_i u(t) + c_i    if    (x(t), u(t)) \in D_i,
    given the mode sequence z = (z(0), ... , z(N-1)), returns the matrices A_bar, B_bar, c_bar such that
    x_bar = A_bar x(0) + B_bar u_bar + c_bar
    with x_bar = (x(0), ... , x(N)) and u_bar = (u(0), ... , u(N-1)).
    """

    # system dimensions
    n_x = affine_systems[0].n_x
    n_u = affine_systems[0].n_u
    N = len(switching_sequence)

    # matrix sequence
    A_sequence = [affine_systems[switching_sequence[i]].A for i in range(N)]
    B_sequence = [affine_systems[switching_sequence[i]].B for i in range(N)]
    c_sequence = [affine_systems[switching_sequence[i]].c for i in range(N)]

    # free evolution of the system
    A_bar = np.vstack([productory(A_sequence[i::-1]) for i in range(N)])
    A_bar = np.vstack((np.eye(n_x), A_bar))

    # forced evolution of the system
    B_bar = np.zeros((n_x*N,n_u*N))
    for i in range(N):
        for j in range(i):
            B_bar[n_x*i:n_x*(i+1), n_u*j:n_u*(j+1)] = productory(A_sequence[i:j:-1]).dot(B_sequence[j])
        B_bar[n_x*i:n_x*(i+1), n_u*i:n_u*(i+1)] = B_sequence[i]
    B_bar = np.vstack((np.zeros((n_x, n_u*N)), B_bar))

    # evolution related to the offset term
    c_bar = np.vstack((np.zeros((n_x,1)), c_sequence[0]))
    for i in range(1, N):
        offset_i = sum([productory(A_sequence[i:j:-1]).dot(c_sequence[j]) for j in range(i)]) + c_sequence[i]
        c_bar = np.vstack((c_bar, offset_i))

    return A_bar, B_bar, c_bar

def zero_order_hold(A, B, c, h):

    # system dimensions
    n_x = np.shape(A)[0]
    n_u = np.shape(B)[1]

    # zero order hold
    mat_c = np.zeros((n_x+n_u+1, n_x+n_u+1))
    mat_c[0:n_x,:] = np.hstack((A, B, c))
    mat_d = linalg.expm(mat_c*h)

    # discrete time dynamics
    A_d = mat_d[0:n_x, 0:n_x]
    B_d = mat_d[0:n_x, n_x:n_x+n_u]
    c_d = mat_d[0:n_x, n_x+n_u:n_x+n_u+1]

    return A_d, B_d, c_d

def explicit_euler(A, B, c, h):
    A_d = A*h + np.eye(A.shape[0])
    B_d = B*h
    c_d = c*h
    return A_d, B_d, c_d

# def semi_implicit_euler(A_q, A_v, B_v, c_v, h):
#     n = A_q.shape[0]
#     A_d = np.vstack((
#         np.hstack((np.eye(n) + (h**2)*A_q, h*np.eye(n) + (h**2)*A_v)),
#         np.hstack((h*A_q, np.eye(n) + h*A_v))
#             ))
#     B_d = np.vstack(((h**2)*B_v, h*B_v))
#     c_d = np.vstack(((h**2)*c_v, h*c_v))
#     return A_d, B_d, c_d

def dare(A, B, Q, R):
    # cost to go
    P = linalg.solve_discrete_are(A, B, Q, R)
    # optimal gain
    K = - linalg.inv(B.T.dot(P).dot(B)+R).dot(B.T).dot(P).dot(A)
    return P, K

def moas_closed_loop(A, B, K, D):
    # closed loop dynamics
    A_cl = A + B.dot(K)
    # constraints for the maximum output admissible set
    lhs_cl = D.lhs_min[:,:A.shape[0]] + D.lhs_min[:,A.shape[0]:].dot(K)
    rhs_cl = D.rhs_min
    X_cl = Polytope(lhs_cl, rhs_cl)
    X_cl.assemble()
    # compute maximum output admissible set
    return moas(A_cl, X_cl)

def moas_closed_loop_from_orthogonal_domains(A, B, K, X, U):
    lhs = linalg.block_diag(X.lhs_min, U.lhs_min)
    rhs = np.vstack((X.rhs_min, U.rhs_min))
    D = Polytope(lhs, rhs)
    D.assemble()
    return moas_closed_loop(A, B, K, D)

def moas(A, X, tol=1.e-9):
    """
    Returns the maximum output admissible set (see Gilbert, Tan - Linear Systems with State and Control Constraints, The Theory and Application of Maximal Output Admissible Sets) for a non-actuated linear system with state constraints (the output vector is supposed to be the entire state of the system, i.e. y=x and C=I).

    INPUTS:
        A: state transition matrix
        X: constraint polytope X.lhs * x <= X.rhs

    OUTPUTS:
        moas: maximum output admissible set (instatiated as a polytope)
    """

    # ensure that the system is stable (otherwise the algorithm doesn't converge)
    eig_max = np.max(np.absolute(np.linalg.eig(A)[0]))
    if eig_max > 1.:
        raise ValueError('Cannot compute MOAS for unstable systems')

    # Gilber and Tan algorithm
    print('Computation of Maximal Invariant Constraint-Admissible Set started.')
    [n_constraints, n_variables] = X.lhs_min.shape
    t = 0
    convergence = False
    while convergence == False:

        # cost function gradients for all i
        J = X.lhs_min.dot(np.linalg.matrix_power(A,t+1))

        # constraints to each LP
        cons_lhs = np.vstack([X.lhs_min.dot(np.linalg.matrix_power(A,k)) for k in range(t+1)])
        cons_rhs = np.vstack([X.rhs_min for k in range(t+1)])

        # list of all minima
        J_sol = []
        for i in range(n_constraints):
            sol = linear_program(np.reshape(-J[i,:], (n_variables,1)), cons_lhs, cons_rhs)
            J_sol.append(-sol.min - X.rhs_min[i])

        # convergence check
        print 'Determinedness index: ' + str(t) + ', Convergence index: ' + str(np.max(J_sol)) + ', Number of facets: ' + str(cons_lhs.shape[0]) + '.                \r',
        if np.max(J_sol) < tol:
            convergence = True
        else:
            t += 1

    # define polytope
    print '\nMaximal Invariant Constraint-Admissible Set found.'
    print 'Removing redundant facets ...',
    moas = Polytope(cons_lhs, cons_rhs)
    moas.assemble()
    print 'minimal facets are ' + str(moas.lhs_min.shape[0]) + '.'

    return moas