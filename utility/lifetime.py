"""Plan-based column lifetimes and address-based slab reclamation."""
import gc

import torch

MASK_COL_START = 10_000


def slab_blocks(tensor, pool):
    if tensor.numel() == 0 or tensor.device != pool._mem_pool.device:
        return None
    offset = tensor.data_ptr() - pool._mem_pool.data_ptr()
    span = 1 + sum((size - 1) * stride for size, stride in zip(tensor.size(), tensor.stride()))
    end = offset + span * tensor.element_size()
    if 0 <= offset < end <= pool._mem_pool.numel():
        return offset // pool.block_size_bytes, (end - 1) // pool.block_size_bytes
    return None


def last_uses(ops, args):
    last = {}
    for j, u in enumerate(ops):
        op = args[u]
        reads = [] if op['name'] == 'Scan' else op.get('input ids', op.get('output ids', []))
        for col in reads:
            last[col] = j
    return last


def free_dead(tensor_group, pool, last, j, poison=False, check_refs=False):
    for col in list(tensor_group):
        if col < MASK_COL_START and last.get(col, -1) <= j:
            del tensor_group[col]

    covered = set()
    for variable in tensor_group.values():
        blocks = slab_blocks(variable.tensor, pool)
        if blocks is not None:
            covered.update(range(blocks[0], blocks[1] + 1))

    freed = []
    for start, count in list(pool._allocs.items()):
        if covered.isdisjoint(range(start, start + count)):
            if poison:
                pool._mem_pool[start * pool.block_size_bytes:(start + count) * pool.block_size_bytes].fill_(255)
            pool.free(start)
            freed.append(start)

    if check_refs:
        # Pipeline closures may form unreachable cycles; only surviving references matter.
        gc.collect()
        for obj in gc.get_objects():
            if isinstance(obj, torch.Tensor) and obj is not pool._mem_pool and obj.is_cuda:
                blocks = slab_blocks(obj, pool)
                if blocks is not None and not covered.issuperset(range(blocks[0], blocks[1] + 1)):
                    raise RuntimeError(f'Uncovered slab tensor: shape={tuple(obj.shape)}, blocks={blocks}')
    return freed
