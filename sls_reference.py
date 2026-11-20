"""Bound Rayleigh poles for one ISO SLS layer over a half-space, or a half-space.

Use exp(i omega t - i k x), z positive down, attenuation = -Im(k).
Continue each lossless disba root into the complex plane. No leaky-root search.
"""
import numpy as np
from scipy.optimize import root

from imaging import theoretical_modes
from scripts.independent_sls import material, complex_modulus


def boundary_matrix(k, scale, omega, layers, moduli):
    # Columns are P/S potentials. Anchor each finite-layer exponential at
    # the face from which it decays; the half-space has only decaying waves.
    mu_scale = abs(moduli[0][1])
    blocks = []
    nearest_branch = np.inf
    for (h, _, _, rho), (bulk, mu) in zip(layers, moduli):
        q = np.sqrt(k*k - rho*omega**2 / np.array([bulk+mu, mu], complex))
        nearest_branch = min(nearest_branch, np.min(abs(q))/scale)
        def basis(s):
            px, sx = -1j*k, -s[1]
            pz, sz = s[0], -1j*k
            return np.array([[px, sx], [pz, sz],
                [mu*(s[0]*px-1j*k*pz), mu*(s[1]*sx-1j*k*sz)],
                [(bulk-mu)*(-1j*k*px)+(bulk+mu)*s[0]*pz, -2j*k*mu*s[1]]]
                ) / np.array([scale, scale, mu_scale*scale**2, mu_scale*scale**2])[:,None]
        down = basis(-q)
        if h:
            up = basis(q)
            propagation = np.exp(-q*h)
            blocks.append((np.column_stack((down, up*propagation)), np.column_stack((down*propagation, up))))
        else:
            half = down
    if not blocks:
        return half[2:], nearest_branch
    top, bottom = blocks[0]
    return np.block([[top[2:], np.zeros((2,2))], [bottom, -half]]), nearest_branch


def sls_modes(c, layers, freq, max_mode):
    if len(layers) not in (1, 2) or len(c.get('material_properties', [])) != len(layers):
        raise ValueError('当前 SLS 界面匹配仅支持均匀半空间或一层覆盖层加半空间，且各层需有 Qκ、Qμ。')
    att = c['attenuation']
    specs = [material(rho, vp, vs, p['qkappa'], p['qmu'], c['f0_hz'], att['n_sls'], att['velocity_reference'])
             for (_, vp, vs, rho), p in zip(layers, c['material_properties'])]
    seeds, _ = theoretical_modes(dict(layers=layers), freq, max_mode)
    curves, diagnostics = {}, {}
    by_frequency = {}
    for mode, (ff, vv) in seeds.items():
        points = []
        for f, velocity in zip(ff, vv):
            omega = 2*np.pi*f
            scale = omega/velocity
            if f not in by_frequency:
                by_frequency[f] = np.array([[complex_modulus(s[name], f) for name in ('kappa','mu')] for s in specs])
            targets = by_frequency[f]
            elastic = np.array([[rho*(vp*vp-vs*vs), rho*vs*vs] for _,vp,vs,rho in layers])
            z = 1+0j
            # Small continuation steps retain the branch seeded in the elastic limit.
            for fraction in (.25, .5, .75, 1.):
                moduli = elastic + fraction*(targets-elastic)
                def residual(pair):
                    trial = scale*complex(*pair)
                    determinant = np.linalg.det(boundary_matrix(trial,scale,omega,layers,moduli)[0])
                    return (determinant.real, determinant.imag)
                r = root(residual, (z.real,z.imag), tol=1e-10)
                candidate = complex(*r.x)
                if not r.success or abs(candidate-z) > .05 or not np.isfinite(candidate):
                    break
                z = candidate
            else:
                k = scale*z
                matrix, branch = boundary_matrix(k, scale, omega, layers, targets)
                sv = np.linalg.svd(matrix, compute_uv=False)
                misfit = float(sv[-1]/sv[0])
                # Retain only trapped continuations, away from body-wave branch points.
                if misfit > 1e-8 or branch < 1e-4 or k.imag > 1e-9*scale or k.real <= 0:
                    continue
                if k.real <= (omega*np.sqrt(layers[-1,3]/targets[-1,1])).real:
                    continue
                if any(abs(k-old[1]) < 1e-6*scale for other in diagnostics.values() for old in other if old[0]==f):
                    continue
                points.append((float(f),k,misfit))
        if points:
            diagnostics[mode] = points
            curves[mode] = ([p[0] for p in points], [2*np.pi*p[0]/p[1].real for p in points])
    provenance = dict(backend='SLS complex-k', convention='exp(i omega t - i k x); c=omega/Re(k)',
        material='plane strain SLS; kappa=lambda+mu; native log-pole fit', seed='disba elastic branches',
        scope='ISO homogeneous or two-material column; trapped continuations only',
        attenuation_per_m={str(m):[-float(p[1].imag) for p in pts] for m,pts in diagnostics.items()},
        boundary_relative_residual={str(m):[p[2] for p in pts] for m,pts in diagnostics.items()})
    return curves, provenance
