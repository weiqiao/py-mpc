import gurobipy as grb
import numpy as np
from collections import namedtuple

LPSolution = namedtuple('LPSolution',  ['argmin', 'min', 'active_set', 'inequality_multipliers', 'equality_multipliers', 'primal_degenerate', 'dual_degenerate'])

def linear_program(f, A=None, b=None, C=None, d=None, tol=1.e-7):
    """
    Solves the linear program
    min  f^T x
    s.t. A x <= b
         C x  = d
         x_lb <= x <= x_ub

    OUTPUTS:
        x_star: argument which minimizes the cost (=nan if the LP is unfeasible or unbounded)
        V_star: minimum of the cost function (=nan if the LP is unfeasible or unbounded)
    """

    # initialize gurobi model
    model = grb.Model()

    # optimization variables
    n_x = f.shape[0]
    x = model.addVars(n_x, lb=[- grb.GRB.INFINITY]*n_x, name='x')
    x_np = np.array([[x[i]] for i in range(n_x)])

    with suppress_stdout():

        # inequality constraints
        n_ineq = 0
        if A is not None and b is not None:
            n_ineq = A.shape[0]
            expr = A.dot(x_np) - b
            inequalities = model.addConstrs((expr[i,0] <= 0. for i in range(n_ineq)))

        # equality constraints
        n_eq = 0
        if C is not None and d is not None:
            n_eq = C.shape[0]
            expr = C.dot(x_np) - d
            equalities = model.addConstrs((expr[i,0] == 0. for i in range(n_eq)))

        # cost function
        f = np.reshape(f, (n_x, 1))
        V = f.T.dot(x_np)[0,0]
        model.setObjective(V)

    # initialize output
    x_star = np.full((n_x,1), np.nan)
    V_star = np.nan
    active_set = None
    mult_ineq = np.full((n_ineq,1), np.nan)
    mult_eq = np.full((n_eq,1), np.nan)
    primal_degenerate = None
    dual_degenerate = None

    # run the optimization
    model.setParam('OutputFlag', False)
    #model.setParam(grb.GRB.Param.OptimalityTol, 1.e-9)
    #model.setParam(grb.GRB.Param.FeasibilityTol, 1.e-9)
    model.optimize()
    # model.Runtime
    
    # populate output
    #print model.status
    if model.status == grb.GRB.Status.OPTIMAL:
        x_star = np.array([[model.getAttr('x', x)[i]] for i in range(n_x)])
        V_star = V.getValue()

        # inequality constraints
        if A is not None and b is not None:
            mult_ineq = np.array([[-model.getAttr('Pi', inequalities)[i]] for i in range(n_ineq)])
            active_set = [i for i in range(n_ineq) if model.getAttr('CBasis', inequalities)[i] == -1]
            primal_degenerate = len(active_set) > n_x - n_eq
            dual_degenerate = len(list(np.where(mult_ineq < tol)[0])) > n_ineq - n_x + n_eq

        # equality constraints
        if C is not None and d is not None:
            mult_eq = np.array([[-model.getAttr('Pi', equalities)[i]] for i in range(n_eq)])

    return LPSolution(
        argmin = x_star,
        min = V_star,
        inequality_multipliers = mult_ineq,
        equality_multipliers = mult_eq,
        active_set = active_set,
        primal_degenerate = primal_degenerate,
        dual_degenerate = dual_degenerate)


def quadratic_program(H, f=None, A=None, b=None, C=None, d=None, x_lb=None, x_ub=None):
    """
    Solves the strictly convex (H > 0) quadratic program
    min  .5 x^T H x + f^T x
    s.t. A x <= b
         C x  = d
         x_lb <= x <= x_ub

    OUTPUTS:
        x_star: argument which minimizes the cost (=nan if the LP is unfeasible or unbounded)
        V_star: minimum of the cost function (=nan if the LP is unfeasible or unbounded)
    """

    # initialize gurobi model
    model = grb.Model()

    # optimization variables
    n_x = H.shape[0]
    if x_lb is None:
        x_lb = [- grb.GRB.INFINITY]*n_x
    if x_ub is None:
        x_ub = [grb.GRB.INFINITY]*n_x
    x = model.addVars(n_x, lb=x_lb, ub=x_ub, name='x')
    x_np = np.array([[x[i]] for i in range(n_x)])

    with suppress_stdout():

        # inequality constraints
        if A is not None and b is not None:
            expr = A.dot(x_np) - b
            model.addConstrs((expr[i,0] <= 0. for i in range(A.shape[0])))

        # equality constraints
        if C is not None and d is not None:
            expr = C.dot(x_np) - d
            model.addConstrs((expr[i,0] == 0. for i in range(C.shape[0])))

        # cost function
        f = np.reshape(f, (n_x, 1))
        V = .5*x_np.T.dot(H).dot(x_np)[0,0] + f.T.dot(x_np)[0,0]
        model.setObjective(V)

    # run the optimization
    model.setParam('OutputFlag', False)
    model.setParam(grb.GRB.Param.OptimalityTol, 1.e-9)
    model.setParam(grb.GRB.Param.FeasibilityTol, 1.e-9)
    model.optimize()

    # return the result
    if model.status == grb.GRB.Status.OPTIMAL:
        x_star = np.array([[model.getAttr('x', x)[i]] for i in range(n_x)])
        V_star = V.getValue()
    else:
        x_star = np.full((n_x,1), np.nan)
        V_star = np.nan
        
    return x_star, V_star

def quadratically_constrained_linear_program(f, A=None, b=None, C=None, d=None, P=None, q=None, r=None, tol=1.e-9):

    # initialize gurobi model
    model = grb.Model()

    # optimization variables
    n_x = f.shape[0]
    x = model.addVars(n_x, lb=[- grb.GRB.INFINITY]*n_x, name='x')

    # linear inequalities
    if A is not None and b is not None:
        for i in range(A.shape[0]):
            lhs = grb.LinExpr()
            for j in range(n_x):
                if np.abs(A[i,j]) > tol:
                    lhs.add(A[i,j]*x[j])
            model.addConstr(lhs <= b[i])

    # linear equalities
    if C is not None and d is not None:
        for i in range(C.shape[0]):
            lhs = grb.LinExpr()
            for j in range(n_x):
                if np.abs(C[i,j]) > tol:
                    lhs.add(C[i,j]*x[j])
            model.addConstr(lhs == d[i])

    # quadratic inequalities
    lhs = grb.QuadExpr()
    for i in range(n_x):
        for j in range(n_x):
            if np.abs(P[i,j]) > tol:
                lhs.add(x[i]*P[i,j]*x[j])
    if q is not None:
        for i in range(n_x):
            if np.abs(q[i,0]) > tol:
                lhs.add(q[i,0]*x[i])
    model.addConstr(lhs <= r)

    # cost function
    f = np.reshape(f, (n_x, 1))
    cost = grb.LinExpr()
    for i in range(n_x):
        if np.abs(f[i,0]) > tol:
            cost.add(f[i,0]*x[i])
    model.setObjective(cost)

    # run the optimization
    model.setParam('OutputFlag', False)
    model.setParam(grb.GRB.Param.OptimalityTol, 1.e-9)
    model.setParam(grb.GRB.Param.FeasibilityTol, 1.e-9)
    model.optimize()

    x_star = np.array([[model.getAttr('x', x)[i]] for i in range(n_x)])
    V_star = cost.getValue()

    return x_star, V_star

def read_status(status):
    status = str(status)
    table = {
        '2': 'OPTIMAL',
        '3': 'INFEASIBLE',
        '4': 'INFEASIBLE OR UNBOUNDED',
        '9': 'TIME LIMIT',
        '11': 'INTERRUPTED',
        '13': 'SUBOPTIMAL',
        }
    return table[status]

def real_variable(model, d_list, **kwargs):
    """
    Creates a Gurobi variable with dimension d_list (e.g., [3,4,5]) with minus infinity as lower bounds.
    """
    lb_x = [-grb.GRB.INFINITY]
    for d in d_list:
        lb_x = [lb_x * d]
    x = model.addVars(*d_list, lb=lb_x, **kwargs)
    return x, model

import sys, os
from contextlib import contextmanager

@contextmanager
def suppress_stdout():
    with open(os.devnull, "w") as devnull:
        old_stdout = sys.stdout
        sys.stdout = devnull
        try:  
            yield
        finally:
            sys.stdout = old_stdout
