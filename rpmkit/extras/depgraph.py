#
# Copyright (C) 2013 Red Hat, Inc.
# Red Hat Author(s): Satoru SATOH <ssato@redhat.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
from rpmkit.memoize import memoize

import rpmkit.rpmutils as RU
import rpmkit.utils as U
import networkx as NX


def _make_dependency_graph_with_nx(root, reversed=True, rreqs=None):
    """
    Make RPM dependency graph with using Networkx.DiGraph for given root.

    :param root: RPM Database root dir
    :param reversed: Resolve reversed dependency from required to requires
    :param rreqs: A dict represents RPM dependencies;
        {x: [package_requires_x]} or {x: [package_required_by_x]}.

    :return: networkx.DiGraph instance
    """
    G = NX.DiGraph()

    if rreqs is None:
        rreqs = RU.make_requires_dict(root, reversed) 

    G.add_nodes_from(rreqs.keys())
    for k, vs in rreqs.iteritems():
        G.add_edges_from([(k, v) for v in vs])

    return G


make_dependency_graph_with_nx = memoize(_make_dependency_graph_with_nx)


def list_strongly_connected_rpms(root, limit=1, reversed=True, rreqs=None):
    """
    :param root: RPM Database root dir
    :param limit: Results of which length of list of RPM names less than this
        ``limit`` + 1 will be ignored.
    :param reversed: Resolve reversed dependency from required to requires
    :param rreqs: A dict represents RPM dependencies;
        {x: [package_requires_x]} or {x: [package_required_by_x]}.

    :return: [[rpm_name]]; Each list represents strongly connected RPMs.
    """
    G = make_dependency_graph_with_nx(root, reversed, rreqs)
    return [xs for xs in NX.strongly_connected_components(G) if len(xs) > 1]


def list_rpms_having_cyclic_dependencies(root, reversed=True, rreqs=None):
    """
    :param root: RPM Database root dir
    :param reversed: Resolve reversed dependency from required to requires
    :param rreqs: A dict represents RPM dependencies;
        {x: [package_requires_x]} or {x: [package_required_by_x]}.

    :return: [[rpm_name]]; Each list represents strongly connected RPMs.
    """
    G = make_dependency_graph_with_nx(root, reversed, rreqs)
    return NX.simple_cycles(G)


def _degenerate_node(nodes, sep='|'):
    """
    :param nodes: List of strongly connected nodes :: [str]
    :return: Degenerated node :: str
    """
    return sep.join(nodes)


def _degenerate_nodes(G, nodes, reqs, rreqs, sep='|'):
    """
    For each node, remove edges from/to that node and the node from the graph
    ``G`` and then add newly 'degenerated' node and relevant edges again.

    :param G: Dependency graph of nodes
    :param nodes: Node (name) list
    """
    for node in nodes:
        G.remove_edges_from([(node, p) for p in rreqs.get(node, [])])
        G.remove_edges_from([(r, node) for r in reqs.get(node, [])])

    G.remove_nodes_from(nodes)

    dnode = _degenerate_node(nodes)
    G.add_node(dnode)

    dnode_rreqs = U.uconcat([p for p in rreqs.get(node, []) if p not in nodes]
                            for node in nodes)
    dnode_reqs = U.uconcat([r for r in reqs.get(node, []) if r not in nodes]
                           for node in nodes)

    if dnode_rreqs:
        G.add_edges_from([(dnode, p) for p in dnode_rreqs])

    if dnode_reqs:
        G.add_edges_from([(r, dnode) for r in dnode_reqs])

    return G


def make_rpm_dependencies_dag(root, reqs=None, rreqs=None):
    """
    Make direct acyclic graph from RPM dependencies.

    see also:

    * http://en.wikipedia.org/wiki/Directed_acyclic_graph
    * http://en.wikipedia.org/wiki/Strongly_connected_component

    :param root: RPM Database root dir
    :param rreqs: A dict represents RPM dependencies;
        {x: [package_requires_x]} or {x: [package_required_by_x]}.

    :return: networkx.DiGraph instance represents the dag of rpm deps.
    """
    if rreqs is None:
        rreqs = RU.make_reversed_requires_dict(root)

    if reqs is None:
        reqs = RU.make_requires_dict(root)

    G = make_dependency_graph_with_nx(root, rreqs=rreqs)

    # Remove edges of self cyclic nodes:
    G.remove_edges_from(G.selfloop_edges())

    # Degenerate strongly connected components:
    for scc in NX.strongly_connected_components(G):
        scc = sorted(U.uniq(scc))

        if len(scc) == 1:  # Ignore sccs of which length is 1.
            continue

        G = _degenerate_nodes(G, scc, reqs, rreqs, '|')

    # Degenerate cyclic nodes:
    for cns in NX.simple_cycles(G):
        cns = sorted(U.uniq(cns))

        # Should not happen as selc cyclic nodes were removed in advance.
        assert len(cns) != 1, "Self cyclic node: " + cns[0]

        G = _degenerate_nodes(G, cns, reqs, rreqs, ',')

    assert NX.is_directed_acyclic_graph(G), \
           "I'm still missing something to make it dag..."

    return G

# vim:sw=4:ts=4:et:
