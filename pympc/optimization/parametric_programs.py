import numpy as np
from pympc.optimization.pnnls import linear_program
from pympc.optimization.gurobi import quadratic_program
from pympc.geometry.polytope import Polytope


class ParametricLP:

    def __init__(self, F_u, F_x, F, C_u, C_x, C):
        """
        LP in the form:
        min  \sum_i | (F_u u + F_x x + F)_i |
        s.t. C_u u <= C_x x + C
        """
        self.F_u = F_u
        self.F_x = F_x
        self.F = F
        self.C_u = C_u
        self.C_x = C_x
        self.C = C
        self.add_slack_variables()
        return

    def add_slack_variables(self):
        """
        Reformulates the LP as:
        min f^T z
        s.t. A z <= B x + c
        """
        n_slack = self.F.shape[0]
        n_u = self.F_u.shape[1]
        self.f = np.vstack((
            np.zeros((n_u,1)),
            np.ones((n_slack,1))
            ))
        self.A = np.vstack((
            np.hstack((self.C_u, np.zeros((self.C_u.shape[0], n_slack)))),
            np.hstack((self.F_u, -np.eye(n_slack))),
            np.hstack((-self.F_u, -np.eye(n_slack)))
            ))
        self.B = np.vstack((self.C_x, -self.F_x, self.F_x))
        self.c = np.vstack((self.C, -self.F, self.F))
        self.n_var = n_u + n_slack
        self.n_cons = self.A.shape[0]
        return

    def solve(self, x0, u_length=None):
        x0 = np.reshape(x0, (x0.shape[0], 1))
        sol = linear_program(self.f, self.A, self.B.dot(x0)+self.c)
        u_star = sol.argmin[0:self.F_u.shape[1]]
        if u_length is not None:
            if not float(u_star.shape[0]/u_length).is_integer():
                raise ValueError('Uncoherent dimension of the input u_length.')
            u_star = [u_star[i*u_length:(i+1)*u_length,:] for i in range(u_star.shape[0]/u_length)]
        return u_star, sol.min


class ParametricQP:

    def __init__(self, F_uu, F_xu, F_xx, F_u, F_x, F, C_u, C_x, C):
        """
        Multiparametric QP in the form:
        min  .5 u' F_{uu} u + x0' F_{xu} u + F_u' u + .5 x0' F_{xx} x0 + F_x' x0 + F
        s.t. C_u u <= C_x x + C
        """
        self.F_uu = F_uu
        self.F_xx = F_xx
        self.F_xu = F_xu
        self.F_u = F_u
        self.F_x = F_x
        self.F = F
        self.C_u = C_u
        self.C_x = C_x
        self.C = C
        self.remove_linear_terms()
        self._feasible_set = None
        return

    def solve(self, x0, u_length=None):
        x0 = np.reshape(x0, (x0.shape[0], 1))
        H = self.F_uu
        f = x0.T.dot(self.F_xu) + self.F_u.T
        A = self.C_u
        b = self.C + self.C_x.dot(x0)
        u_star, cost = quadratic_program(H, f, A, b)
        cost += .5*x0.T.dot(self.F_xx).dot(x0) + self.F_x.T.dot(x0) + self.F
        if u_length is not None:
            if not float(u_star.shape[0]/u_length).is_integer():
                raise ValueError('Uncoherent dimension of the input u_length.')
            u_star = [u_star[i*u_length:(i+1)*u_length,:] for i in range(u_star.shape[0]/u_length)]
        return u_star, cost[0,0]

    def get_active_set(self, x, u, tol=1.e-6):
        u = np.vstack(u)
        return tuple(np.where((self.C_u.dot(u) - self.C - self.C_x.dot(x)) > -tol)[0])

    def remove_linear_terms(self):
        """
        Applies the change of variables z = u + F_uu^-1 (F_xu' x + F_u')
        that puts the cost function in the form
        V = 1/2 z' H z + 1/2 x' F_xx_q x + F_x_q' x + F_q
        and the constraints in the form:
        G u <= W + S x
        """
        self.H_inv = np.linalg.inv(self.F_uu)
        self.H = self.F_uu
        self.F_xx_q = self.F_xx - self.F_xu.dot(self.H_inv).dot(self.F_xu.T)
        self.F_x_q = self.F_x - self.F_xu.dot(self.H_inv).dot(self.F_u)
        self.F_q = self.F - .5*self.F_u.T.dot(self.H_inv).dot(self.F_u)
        self.G = self.C_u
        self.S = self.C_x + self.C_u.dot(self.H_inv).dot(self.F_xu.T)
        self.W = self.C + self.C_u.dot(self.H_inv).dot(self.F_u)
        return

    @property
    def feasible_set(self):
        if self._feasible_set is None:
            augmented_polytope = Polytope(np.hstack((- self.C_x, self.C_u)), self.C)
            augmented_polytope.assemble()
            if augmented_polytope.empty:
                return None
            self._feasible_set = augmented_polytope.orthogonal_projection(range(self.C_x.shape[1]))
        return self._feasible_set


    def get_z_sensitivity(self, active_set):
        # clean active set
        G_A = self.G[active_set,:]
        if active_set and np.linalg.matrix_rank(G_A) < G_A.shape[0]:
            lir = linearly_independent_rows(G_A)
            active_set = [active_set[i] for i in lir]

        # multipliers explicit solution
        inactive_set = sorted(list(set(range(self.C.shape[0])) - set(active_set)))
        [G_A, W_A, S_A] = [self.G[active_set,:], self.W[active_set,:], self.S[active_set,:]]
        [G_I, W_I, S_I] = [self.G[inactive_set,:], self.W[inactive_set,:], self.S[inactive_set,:]]
        H_A = np.linalg.inv(G_A.dot(self.H_inv).dot(G_A.T))
        lambda_A_offset = - H_A.dot(W_A)
        lambda_A_linear = - H_A.dot(S_A)

        # primal variables explicit solution
        z_offset = - self.H_inv.dot(G_A.T.dot(lambda_A_offset))
        z_linear = - self.H_inv.dot(G_A.T.dot(lambda_A_linear))
        return z_offset, z_linear

    def get_u_sensitivity(self, active_set):
        z_offset, z_linear = self.get_z_sensitivity(active_set)

        # primal original variables explicit solution
        u_offset = z_offset - self.H_inv.dot(self.F_u)
        u_linear = z_linear - self.H_inv.dot(self.F_xu.T)
        return u_offset, u_linear

    def get_cost_sensitivity(self, x_list, active_set):
        z_offset, z_linear = self.get_z_sensitivity(active_set)

        # optimal value function explicit solution: V_star = .5 x' V_quadratic x + V_linear x + V_offset
        V_quadratic = z_linear.T.dot(self.H).dot(z_linear) + self.F_xx_q
        V_linear = z_offset.T.dot(self.H).dot(z_linear) + self.F_x_q.T
        V_offset = self.F_q + .5*z_offset.T.dot(self.H).dot(z_offset)

        # tangent approximation
        plane_list = []
        for x in x_list:
            A = x.T.dot(V_quadratic) + V_linear
            b = -.5*x.T.dot(V_quadratic).dot(x) + V_offset
            plane_list.append([A, b])

        return plane_list

    def solve_free_x(self):
        H = np.vstack((
            np.hstack((self.F_uu, self.F_xu.T)),
            np.hstack((self.F_xu, self.F_xx))
            ))
        f = np.vstack((self.F_u, self.F_x))
        A = np.hstack((self.C_u, -self.C_x))
        b = self.C
        z_star, cost = quadratic_program(H, f, A, b)
        u_star = z_star[0:self.F_uu.shape[0],:]
        x_star = z_star[self.F_uu.shape[0]:,:]
        return u_star, x_star, cost