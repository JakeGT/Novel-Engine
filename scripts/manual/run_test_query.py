import sys
import pprint
from backend.graph.manager import graph_manager
from backend.graph.policies import get_policy
from backend.graph.traversal import context_traversal

try:
    policy = get_policy("scene_generation")
    res = context_traversal.traverse(["char_black_knight"], policy)
    pprint.pprint(res)
except Exception as e:
    print("ERROR:", e)
