#!/usr/bin/env python
##
# @file
# This file is part of SeisSol.
#
# @author Carsten Uphoff (c.uphoff AT tum.de, http://www5.in.tum.de/wiki/index.php/Carsten_Uphoff,_M.Sc.)
#
# @section LICENSE
# Copyright (c) 2016, SeisSol Group
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from this
#    software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
# @section DESCRIPTION
#
  
from gemmgen import DB, Tools, Arch, Kernel
import argparse
import DynamicRupture
import Plasticity
import SurfaceDisplacement

cmdLineParser = argparse.ArgumentParser()
cmdLineParser.add_argument('--matricesDir')
cmdLineParser.add_argument('--outputDir')
cmdLineParser.add_argument('--arch')
cmdLineParser.add_argument('--order')
cmdLineParser.add_argument('--numberOfMechanisms')
cmdLineParser.add_argument('--generator')
cmdLineParser.add_argument('--memLayout')
cmdLineParser.add_argument('--dynamicRuptureMethod')
cmdLineParser.add_argument('--PlasticityMethod')
cmdLineArgs = cmdLineParser.parse_args()

architecture = Arch.getArchitectureByIdentifier(cmdLineArgs.arch)
libxsmmGenerator = cmdLineArgs.generator
order = int(cmdLineArgs.order)
numberOfBasisFunctions = Tools.numberOfBasisFunctions(order)
numberOfQuantities = 9

clones = {
  'star': [ 'AstarT', 'BstarT', 'CstarT' ]
}

db = Tools.parseMatrixFile('{}/matrices_{}.xml'.format(cmdLineArgs.matricesDir, numberOfBasisFunctions), clones)

db.insert(DB.MatrixInfo('AplusT', numberOfQuantities, numberOfQuantities))
db.insert(DB.MatrixInfo('AminusT', numberOfQuantities, numberOfQuantities))

DynamicRupture.addMatrices(db, cmdLineArgs.matricesDir, order, cmdLineArgs.dynamicRuptureMethod, numberOfQuantities, numberOfQuantities)
Plasticity.addMatrices(db, cmdLineArgs.matricesDir, cmdLineArgs.PlasticityMethod, order)
SurfaceDisplacement.addMatrices(db, order)

# Load sparse-, dense-, block-dense-config
Tools.memoryLayoutFromFile(cmdLineArgs.memLayout, db, clones)

# Set rules for the global matrix memory order
stiffnessOrder = { 'Xi': 0, 'Eta': 1, 'Zeta': 2 }
globalMatrixIdRules = [
  (r'^k(Xi|Eta|Zeta)DivMT$', lambda x: stiffnessOrder[x[0]]),
  (r'^k(Xi|Eta|Zeta)DivM$', lambda x: 3 + stiffnessOrder[x[0]]),  
  (r'^r(\d{1})DivM$', lambda x: 6 + int(x[0])-1),
  (r'^rT(\d{1})$', lambda x: 10 + int(x[0])-1),
  (r'^fMrT(\d{1})$', lambda x: 14 + int(x[0])-1),
  (r'^fP(\d{1})$', lambda x: 18 + (int(x[0])-1))
]
DB.determineGlobalMatrixIds(globalMatrixIdRules, db)

# Kernels
kernels = list()

db.insert(DB.MatrixInfo('timeIntegrated', numberOfBasisFunctions, numberOfQuantities))
db.insert(DB.MatrixInfo('timeDerivative0', numberOfBasisFunctions, numberOfQuantities))

volume = db['kXiDivM'] * db['timeIntegrated'] * db['AstarT'] \
       + db['kEtaDivM'] * db['timeIntegrated'] * db['BstarT'] \
       + db['kZetaDivM'] * db['timeIntegrated'] * db['CstarT']
kernels.append(Kernel.Prototype('volume', volume))

for i in range(0, 4):
  localFlux = db['r{}DivM'.format(i+1)] * db['fMrT{}'.format(i+1)] * db['timeIntegrated'] * db['AplusT']
  prefetch = None
  if i == 0:
    prefetch = db['timeIntegrated']
  elif i == 1:
    prefetch = localFlux
  else:
    prefetch = Kernel.DummyPrefetch()
  kernels.append(Kernel.Prototype('localFlux[{}]'.format(i), localFlux, prefetch=prefetch))

for i in range(0, 4):
  for j in range(0, 4):
    for h in range(0, 3):
      neighboringFlux = db['r{}DivM'.format(i+1)] * db['fP{}'.format(h+1)] * db['rT{}'.format(j+1)] * db['timeIntegrated'] * db['AminusT']
      kernels.append(Kernel.Prototype('neighboringFlux[{}]'.format(i*12+j*3+h), neighboringFlux, prefetch=db['timeIntegrated']))

for i in range(1, order):
  lastD = 'timeDerivative{}'.format(str(i-1))
  newD  = 'timeDerivative{}'.format(str(i))
  derivative = db['kXiDivMT'] * db[lastD] * db['AstarT'] \
             + db['kEtaDivMT'] * db[lastD] * db['BstarT'] \
             + db['kZetaDivMT'] * db[lastD] * db['CstarT']
  derivative.fitBlocksToSparsityPattern()
  kernels.append(Kernel.Prototype('derivative[{}]'.format(i), derivative, beta=0))
  db.insert(derivative.flat(newD))
  db[newD].fitBlocksToSparsityPattern()
  
DynamicRupture.addKernels(db, kernels, 'timeDerivative0')
Plasticity.addKernels(db, kernels)
SurfaceDisplacement.addKernels(db, kernels)

# Generate code
Tools.generate(cmdLineArgs.outputDir, db, kernels, libxsmmGenerator, architecture)  
