# Copyright (c) 2024, NVIDIA CORPORATION.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import re
from functools import total_ordering

import yaml
from packaging.requirements import InvalidRequirement, Requirement

from .lint import LintMain

RAPIDS_ALPHA_SPEC_PACKAGES = {
    "cubinlinker",
    "cucim",
    "cudf",
    "cugraph",
    "cugraph-dgl",
    "cugraph-equivariant",
    "cugraph-pyg",
    "cuml",
    "cuproj",
    "cuspatial",
    "cuxfilter",
    "dask-cuda",
    "dask-cudf",
    "distributed-ucxx",
    "librmm",
    "libucx",
    "nx-cugraph",
    "ptxcompiler",
    "pylibcugraph",
    "pylibcugraphops",
    "pylibraft",
    "pylibwholegraph",
    "pynvjitlink",
    "raft-dask",
    "rmm",
    "ucx-py",
    "ucxx",
}

RAPIDS_NON_CUDA_SUFFIXED_PACKAGES = {
    "dask-cuda",
}

RAPIDS_CUDA_SUFFIXED_PACKAGES = (
    RAPIDS_ALPHA_SPEC_PACKAGES - RAPIDS_NON_CUDA_SUFFIXED_PACKAGES
)

ALPHA_SPECIFIER = ">=0.0.0a0"

ALPHA_SPEC_OUTPUT_TYPES = {
    "pyproject",
    "requirements",
}

CUDA_SUFFIX_REGEX = re.compile(r"^(?P<package>.*)-cu[0-9]{2}$")


def node_has_type(node, tag_type):
    return node.tag == f"tag:yaml.org,2002:{tag_type}"


def is_rapids_cuda_suffixed_package(name):
    return any(
        (match := CUDA_SUFFIX_REGEX.search(name)) and match.group("package") == package
        for package in RAPIDS_CUDA_SUFFIXED_PACKAGES
    )


def check_package_spec(linter, args, anchors, used_anchors, node):
    @total_ordering
    class SpecPriority:
        def __init__(self, spec):
            self.spec = spec

        def __eq__(self, other):
            return self.spec == other.spec

        def __lt__(self, other):
            if self.spec == other.spec:
                return False
            if self.spec == ALPHA_SPECIFIER:
                return False
            if other.spec == ALPHA_SPECIFIER:
                return True
            return self.sort_str() < other.sort_str()

        def sort_str(self):
            return "".join(c for c in self.spec if c not in "<>=")

    def create_specifier_string(specifiers):
        return ",".join(sorted(specifiers, key=SpecPriority))

    if node_has_type(node, "str"):
        try:
            req = Requirement(node.value)
        except InvalidRequirement:
            return
        if req.name in RAPIDS_ALPHA_SPEC_PACKAGES or is_rapids_cuda_suffixed_package(
            req.name
        ):
            for key, value in anchors.items():
                if value == node:
                    anchor = key
                    break
            else:
                anchor = None
            if anchor not in used_anchors:
                if anchor is not None:
                    used_anchors.add(anchor)
                has_alpha_spec = any(str(s) == ALPHA_SPECIFIER for s in req.specifier)
                if args.mode == "development" and not has_alpha_spec:
                    linter.add_warning(
                        (node.start_mark.index, node.end_mark.index),
                        f"add alpha spec for RAPIDS package {req.name}",
                    ).add_replacement(
                        (node.start_mark.index, node.end_mark.index),
                        str(
                            (f"&{anchor} " if anchor else "")
                            + req.name
                            + create_specifier_string(
                                {str(s) for s in req.specifier} | {ALPHA_SPECIFIER},
                            )
                        ),
                    )
                elif args.mode == "release" and has_alpha_spec:
                    linter.add_warning(
                        (node.start_mark.index, node.end_mark.index),
                        f"remove alpha spec for RAPIDS package {req.name}",
                    ).add_replacement(
                        (node.start_mark.index, node.end_mark.index),
                        str(
                            (f"&{anchor} " if anchor else "")
                            + req.name
                            + create_specifier_string(
                                {str(s) for s in req.specifier} - {ALPHA_SPECIFIER},
                            )
                        ),
                    )


def check_packages(linter, args, anchors, used_anchors, node):
    if node_has_type(node, "seq"):
        for package_spec in node.value:
            check_package_spec(linter, args, anchors, used_anchors, package_spec)


def check_common(linter, args, anchors, used_anchors, node):
    if node_has_type(node, "seq"):
        for dependency_set in node.value:
            if node_has_type(dependency_set, "map"):
                for dependency_set_key, dependency_set_value in dependency_set.value:
                    if (
                        node_has_type(dependency_set_key, "str")
                        and dependency_set_key.value == "packages"
                    ):
                        check_packages(
                            linter, args, anchors, used_anchors, dependency_set_value
                        )


def check_matrices(linter, args, anchors, used_anchors, node):
    if node_has_type(node, "seq"):
        for item in node.value:
            if node_has_type(item, "map"):
                for matrix_key, matrix_value in item.value:
                    if (
                        node_has_type(matrix_key, "str")
                        and matrix_key.value == "packages"
                    ):
                        check_packages(
                            linter, args, anchors, used_anchors, matrix_value
                        )


def check_specific(linter, args, anchors, used_anchors, node):
    if node_has_type(node, "seq"):
        for matrix_matcher in node.value:
            if node_has_type(matrix_matcher, "map"):
                for matrix_matcher_key, matrix_matcher_value in matrix_matcher.value:
                    if (
                        node_has_type(matrix_matcher_key, "str")
                        and matrix_matcher_key.value == "matrices"
                    ):
                        check_matrices(
                            linter, args, anchors, used_anchors, matrix_matcher_value
                        )


def check_dependencies(linter, args, anchors, used_anchors, node):
    if node_has_type(node, "map"):
        for _, dependencies_value in node.value:
            if node_has_type(dependencies_value, "map"):
                for dependency_key, dependency_value in dependencies_value.value:
                    if node_has_type(dependency_key, "str"):
                        if dependency_key.value == "common":
                            check_common(
                                linter, args, anchors, used_anchors, dependency_value
                            )
                        elif dependency_key.value == "specific":
                            check_specific(
                                linter, args, anchors, used_anchors, dependency_value
                            )


def check_root(linter, args, anchors, used_anchors, node):
    if node_has_type(node, "map"):
        for root_key, root_value in node.value:
            if node_has_type(root_key, "str") and root_key.value == "dependencies":
                check_dependencies(linter, args, anchors, used_anchors, root_value)


class AnchorPreservingLoader(yaml.SafeLoader):
    """A SafeLoader that preserves the anchors for later reference. The anchors can
    be found in the document_anchors member, which is a list of dictionaries, one
    dictionary for each parsed document.
    """

    def __init__(self, stream):
        super().__init__(stream)
        self.document_anchors = []

    def compose_document(self):
        # Drop the DOCUMENT-START event.
        self.get_event()

        # Compose the root node.
        node = self.compose_node(None, None)

        # Drop the DOCUMENT-END event.
        self.get_event()

        self.document_anchors.append(self.anchors)
        self.anchors = {}
        return node


def check_alpha_spec(linter, args):
    loader = AnchorPreservingLoader(linter.content)
    try:
        root = loader.get_single_node()
    finally:
        loader.dispose()
    check_root(linter, args, loader.document_anchors[0], set(), root)


def main():
    m = LintMain()
    m.argparser.description = (
        "Verify that RAPIDS packages in dependencies.yaml do (or do not) have "
        "the alpha spec."
    )
    m.argparser.add_argument(
        "--mode",
        help="mode to use (development has alpha spec, release does not)",
        choices=["development", "release"],
        default="development",
    )
    with m.execute() as ctx:
        ctx.add_check(check_alpha_spec)


if __name__ == "__main__":
    main()
