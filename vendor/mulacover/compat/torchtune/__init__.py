# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

__version__ = '0.4.0+cpu'


# MuLaCover only needs the inference model and module packages.  Importing the
# dataset and recipe stacks here pulls in pyarrow/pandas and can downgrade the
# host application's fsspec, even though none of those packages participate in
# generation.  Keep the upstream package version while leaving submodules to be
# imported explicitly by MuLaCover.
__all__ = ["models", "modules"]
