import numpy as np
from itertools import product
from copy import copy
from pympc.geometry.polytope import Polytope
from pympc.dynamical_systems import DTAffineSystem, DTPWASystem, dare, moas_closed_loop_from_orthogonal_domains
from pympc.control import MPCHybridController
import pympc.plot as mpc_plt
import matplotlib.pyplot as plt

class MovingLimb():
    def __init__(self, A, b, contact_surface):
        norm_factor = np.linalg.norm(A, axis=1)
        self.A = np.divide(A.T, norm_factor).T
        self.b = np.divide(b.T, norm_factor).T
        self.contact_surface = contact_surface
        return

class FixedLimb():
    def __init__(self, position, normal):
        self.position = position
        self.normal = normal/np.linalg.norm(normal)
        return

class BoxAtlas():

    def __init__(
        self,
        topology,
        parameters,
        nominal_limb_positions,
        nominal_limb_forces,
        kinematic_limits,
        velocity_limits,
        force_limits
        ):
        self.topology = topology
        self.parameters = parameters
        self.nominal_limb_positions = nominal_limb_positions
        self.nominal_limb_forces = nominal_limb_forces
        self.kinematic_limits = kinematic_limits
        self.velocity_limits = velocity_limits
        self.force_limits = force_limits
        self.moving_limbs = self.topology['moving'].keys()
        self.fixed_limbs = self.topology['fixed'].keys()
        self.x_eq = self._equilibrium_state()
        self.u_eq = self._equilibrium_input()
        self.n_x = self.x_eq.shape[0]
        self.n_u = self.u_eq.shape[0]
        self.contact_modes = self._get_contact_modes()
        self.affine_systems = self._get_affine_systems()
        self.state_domains, self.input_domains = self._domains()
        self.pwa_system = DTPWASystem.from_orthogonal_domains(
            self.affine_systems,
            self.state_domains,
            self.input_domains)
        return

    def _equilibrium_state(self):
        x_eq = np.zeros((0,1))
        for limb in self.moving_limbs:
            x_eq = np.vstack((x_eq, self.nominal_limb_positions[limb]))
        body_nominal_state = np.zeros((4,1))
        return np.vstack((x_eq, body_nominal_state))

    def _equilibrium_input(self):
        u_eq = np.zeros((len(self.moving_limbs)*2, 1))
        for limb in self.fixed_limbs:
            u_eq = np.vstack((u_eq, self.nominal_limb_forces[limb]))
        return u_eq

    def _get_contact_modes(self):
        modes_tuples = product(*[range(len(self.moving_limbs)) for limb in self.moving_limbs])
        contact_modes = []
        for mode_tuple in modes_tuples:
            mode = dict()
            for i, limb_mode in enumerate(mode_tuple):
                mode[self.moving_limbs[i]] = limb_mode
            contact_modes.append(mode)
        return contact_modes

    def _contact_contribution_A_ct(self, moving_limb):
        if moving_limb.contact_surface is None:
            return np.zeros((2,2))
        else:
            a = moving_limb.A[moving_limb.contact_surface,:]
            A_block = - np.array([
                [a[0]**2, a[0]*a[1]],
                [a[0]*a[1], a[1]**2]
                ]) * self.parameters['stiffness'] / self.parameters['mass']
            return A_block

    def _contact_contribution_B_ct(self, moving_limb):
        if moving_limb.contact_surface is None:
            return np.zeros((2,2))
        else:
            a = moving_limb.A[moving_limb.contact_surface,:]
            B_block = - np.array([
                [a[1]**2, -a[0]*a[1]],
                [-a[0]*a[1], a[0]**2]
                ]) * self.parameters['damping'] / self.parameters['mass']
            return B_block

    def _contact_contribution_c_ct(self, moving_limb):
        if moving_limb.contact_surface is None:
            return np.zeros((2,1))
        else:
            a = moving_limb.A[moving_limb.contact_surface,:]
            b = moving_limb.b[moving_limb.contact_surface,0]
            c_block = self.parameters['stiffness'] * b * np.array([[a[0]],[a[1]]]) / self.parameters['mass']
            return c_block

    def _get_A_ct(self, contact_mode):
        n = len(self.moving_limbs)
        A_upper = np.hstack((
            np.zeros((2*(n+1), 2*(n+1))),
            np.vstack((np.zeros((2*n, 2)), np.eye(2)))
            ))
        A_lower = np.zeros((2, 0))
        for limb, limb_mode in contact_mode.items():
            A_lower = np.hstack((
                A_lower,
                self._contact_contribution_A_ct(self.topology['moving'][limb][limb_mode])
                ))
        A_lower = np.hstack((A_lower, np.zeros((2, 4))))
        return np.vstack((A_upper, A_lower))

    def _get_B_ct(self, contact_mode):
        n_moving = len(self.moving_limbs)
        n_fixed = len(self.fixed_limbs)
        B_upper = np.vstack((
            np.hstack((np.eye(2*n_moving), np.zeros((2*n_moving, 2*n_fixed)))),
            np.zeros((2, 2*(n_moving + n_fixed)))
            ))
        B_lower = np.zeros((2, 0))
        for limb, limb_mode in contact_mode.items():
            B_lower = np.hstack((
                B_lower,
                self._contact_contribution_B_ct(self.topology['moving'][limb][limb_mode])
                ))
        for fixed_limb in self.topology['fixed'].values():
            n = fixed_limb.normal
            B_fixed_limb = np.array([[n[0,0], -n[1,0]],[n[1,0], n[0,0]]])
            B_lower = np.hstack((
                B_lower,
                B_fixed_limb / self.parameters['mass']
                ))
        return np.vstack((B_upper, B_lower))

    def _get_c_ct(self, contact_mode):
        n = len(self.moving_limbs)
        c_upper = np.zeros((2*(n+1), 1))
        c_lower = np.array([[0.],[-self.parameters['gravity']]])
        for limb, limb_mode in contact_mode.items():
            c_lower += self._contact_contribution_c_ct(self.topology['moving'][limb][limb_mode])
        return np.vstack((c_upper, c_lower))

    def _get_affine_systems(self):
        affine_systems = []
        for contact_mode in self.contact_modes:
            A_ct = self._get_A_ct(contact_mode)
            B_ct = self._get_B_ct(contact_mode)
            c_ct = self._get_c_ct(contact_mode)
            a_sys = DTAffineSystem.from_continuous(
                A_ct,
                B_ct,
                c_ct + A_ct.dot(self.x_eq) + B_ct.dot(self.u_eq),
                self.parameters['sampling_time'],
                'explicit_euler'
                )
            affine_systems.append(a_sys)
        return affine_systems

    def _state_constraints(self):
        n = len(self.moving_limbs)
        selection_matrix = np.vstack((np.eye(2), -np.eye(2)))
        X = Polytope(np.zeros((0, 2*(n+2))), np.zeros((0, 1)))
        for i, limb in enumerate(self.moving_limbs):
            lhs = np.hstack((
                    np.zeros((4, i*2)),
                    selection_matrix,
                    np.zeros((4, (n-1-i)*2)),
                    -selection_matrix,
                    np.zeros((4, 2))
                    ))
            rhs = np.vstack((
                self.kinematic_limits[limb]['max'],
                -self.kinematic_limits[limb]['min']
                ))
            X.add_facets(lhs, rhs)
        q_b_max = np.array([[.2],[.2]])
        q_b_min = - q_b_max
        v_b_max = np.ones((2,1))
        v_b_min = - v_b_max
        X.add_bounds(q_b_min, q_b_max, [2*n, 2*n+1])
        X.add_bounds(v_b_min, v_b_max, [2*n+2, 2*n+3])
        X = Polytope(X.A, X.b - X.A.dot(self.x_eq))
        return X

    def _input_constraints(self):
        u_min = np.zeros((0,1))
        u_max = np.zeros((0,1))
        for limb in self.moving_limbs:
            u_min = np.vstack((u_min, self.velocity_limits[limb]['min']))
            u_max = np.vstack((u_max, self.velocity_limits[limb]['max']))
        for limb in self.fixed_limbs:
            u_min = np.vstack((u_min, self.force_limits[limb]['min']))
            u_max = np.vstack((u_max, self.force_limits[limb]['max']))
        U = Polytope.from_bounds(u_min, u_max)
        U = Polytope(U.A, U.b - U.A.dot(self.u_eq))
        return U

    def _contact_mode_constraints(self, contact_mode):
        n = len(self.moving_limbs)
        X = Polytope(np.zeros((0, 2*(n+2))), np.zeros((0, 1)))
        for i, limb in enumerate(self.moving_limbs):
            A = self.topology['moving'][limb][contact_mode[limb]].A
            b = self.topology['moving'][limb][contact_mode[limb]].b
            lhs = np.hstack((
                    np.zeros((A.shape[0], i*2)),
                    A,
                    np.zeros((A.shape[0], 2*(n-i)+2))
                    ))
            X.add_facets(lhs, b)
        X = Polytope(X.A, X.b - X.A.dot(self.x_eq))
        return X

    def _domains(self):
        state_domains = []
        input_domains = []
        X = self._state_constraints()
        U = self._input_constraints()
        U.assemble()
        input_domains = [U] * len(self.contact_modes)
        for contact_mode in self.contact_modes:
            X_mode = self._contact_mode_constraints(contact_mode)
            X_mode.add_facets(X.A, X.b)
            X_mode.assemble()
            state_domains.append(X_mode)
        return state_domains, input_domains