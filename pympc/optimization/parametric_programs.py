import numpy as np
from pympc.optimization.gurobi import linear_program, quadratic_program, linear_expression, quadratic_expression
from pympc.geometry.polytope import Polytope
import gurobipy as grb



class ParametricLP:

    def __init__(self, F_u, F_x, F, C_u, C_x, C):#, row_sparsity=None, column_sparsity=None):
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
        # self.row_sparsity = row_sparsity
        # self.column_sparsity = column_sparsity
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
        # self.cost_blocks = {'F_uu': F_uu, 'F_xx': F_xx, 'F_xu': F_xu, 'F_u': F_u, 'F_x': F_x, 'F': F}
        # self.constraint_blocks = {'C_u': C_u, 'C_x': C_x, 'C': C}
        self.F_uu = F_uu
        self.F_xx = F_xx
        self.F_xu = F_xu
        self.F_u = F_u
        self.F_x = F_x
        self.F = F
        self.C_u = C_u
        self.C_x = C_x
        self.C = C
        self.model, self.quadratic_cost = self.build_model()
        self.remove_linear_terms()
        self._feasible_set = None
        return

    def build_model(self):

        H = np.vstack((
            np.hstack((self.F_uu, self.F_xu.T)),
            np.hstack((self.F_xu, self.F_xx)),
            ))
        f = np.hstack((self.F_u.T, self.F_x.T))
        A = np.hstack((self.C_u, -self.C_x))
        b = self.C

        # initialize model
        model = grb.Model()
        n = H.shape[0]
        ux = model.addVars(n, lb=[- grb.GRB.INFINITY]*n)

        # linear inequalities
        for i, expr in enumerate(linear_expression(A, b, ux)):
            model.addConstr(expr <= 0., name='ineq_'+str(i))

        # cost function
        quadratic_cost = grb.QuadExpr()
        expr = quadratic_expression(H, ux)
        quadratic_cost.add(.5*expr)
        expr = linear_expression(f, np.zeros((1,1)), ux)
        quadratic_cost.add(expr[0])

        model.setParam('OutputFlag', 0)
        model.update()

        return model, quadratic_cost

    def is_feasible(self, x):
        self.set_initial_state(x)
        self.model.setObjective(0.)
        self.model.optimize()
        if self.model.status == grb.GRB.Status.OPTIMAL:
            return True
        return False

    def solve(self, x):
        n_u = self.F_uu.shape[0]
        self.set_initial_state(x)
        self.model.setObjective(self.quadratic_cost)
        self.model.optimize()
        if self.model.status == grb.GRB.Status.OPTIMAL:
            x = self.model.getVars()
            cost = self.model.objVal + self.F[0,0]
            argmin = np.array(self.model.getAttr('x')[:n_u]).reshape(n_u, 1)
        else:
            cost = np.nan
            argmin = np.full((n_u,1), np.nan)
        return argmin, cost

    def set_initial_state(self, x):
        n_u = self.F_uu.shape[0]
        n_x = self.F_xx.shape[0]
        ux = self.model.getVars()
        for i in range(n_x):
            if self.model.getConstrByName('intial_condition_' + str(i)) is not None:
                self.model.remove(self.model.getConstrByName('intial_condition_' + str(i)))
            self.model.addConstr(ux[n_u+i] == x[i,0], name='intial_condition_' + str(i))
        return

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

    def save(self, group_name, super_group=None):

        # open the file
        if super_group is None:
            group = h5py.File(group_name + '.hdf5', 'w')
        else:
            group = super_group.create_group(group_name)

        # write matrices
        F_uu = group.create_dataset('F_uu', self.F_uu.shape)
        F_xu = group.create_dataset('F_xu', self.F_xu.shape)
        F_xx = group.create_dataset('F_xx', self.F_xx.shape)
        F_u = group.create_dataset('F_u', self.F_u.shape)
        F_x = group.create_dataset('F_x', self.F_x.shape)
        F = group.create_dataset('F', self.F.shape)
        C_u = group.create_dataset('C_u', self.C_u.shape)
        C_x = group.create_dataset('C_x', self.C_x.shape)
        C = group.create_dataset('C', self.C.shape)
        F_uu[...] = self.F_uu
        F_xu[...] = self.F_xu
        F_xx[...] = self.F_xx
        F_u[...] = self.F_u
        F_x[...] = self.F_x
        F[...] = self.F
        C_u[...] = self.C_u
        C_x[...] = self.C_x
        C[...] = self.C

        # # sparsity of the Jacobian
        # row_sparsity = group.create_dataset('row_sparsity', (len(self.row_sparsity),))
        # column_sparsity = group.create_dataset('column_sparsity', (len(self.column_sparsity),))
        # row_sparsity[...] = np.array(self.row_sparsity)
        # column_sparsity[...] = np.array(self.column_sparsity)

        # close the file and return
        if super_group is None:
            group.close()
            return
        else:
            return super_group

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


def upload_ParametricQP(group_name, super_group=None):
    """
    Reads the file group_name.hdf5 and generates a ParametricQP from the data therein.
    If a super_group is provided, reads the sub group named group_name which belongs to the super_group.
    """

    # open the file
    if super_group is None:
        qp = h5py.File(group_name + '.hdf5', 'r')
    else:
        qp = super_group[group_name]

    # read matrices
    F_uu = np.array(qp['F_uu'])
    F_xu = np.array(qp['F_xu'])
    F_xx = np.array(qp['F_xx'])
    F_u = np.array(qp['F_u'])
    F_x = np.array(qp['F_x'])
    F = np.array(qp['F'])
    C_u = np.array(qp['C_u'])
    C_x = np.array(qp['C_x'])
    C = np.array(qp['C'])

    # # read sparsity pattern Jacobian
    # row_sparsity = [int(i) for i in qp['row_sparsity']]
    # column_sparsity = [int(i) for i in qp['column_sparsity']]

    # close the file and return
    if super_group is None:
        qp.close()
    return ParametricQP(F_uu, F_xu, F_xx, F_u, F_x, F, C_u, C_x, C)#, row_sparsity, column_sparsity)
