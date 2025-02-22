import argparse
import json
import multiprocessing
from itertools import repeat
import torch

import params
from model.model import PCCoder
from env.env import ProgramEnv
from env.search import cab, cab_repair, dfs, dfs_repair
from dsl.example import Example
from dsl.program import Program
from dsl.value import Value


def load_problems(path):
    problems = []
    with open(path) as fh:
        for line in fh:
            problems.append(json.loads(line.rstrip()))
    return problems


def init_worker(*args):
    global method, counter, fail_counter, model, timeout, max_program_len, max_beam_size
    method, counter, fail_counter, model, timeout, max_program_len, max_beam_size = args


def solve_problems(problems, method, model, timeout, max_program_len, max_beam_size, num_workers, threshold):
    """
    Attempts to predict programs for the given I/O sample sets.
    """
    # Prevents deadlocks due to torch's problems with GPUs on multi processes.
    # This line is here for convenience, but it is recommended to solve problems on CPU since the overhead
    # in this case is minimal.
    torch.set_num_threads(1)

    counter = multiprocessing.Value('i', 0)
    fail_counter = multiprocessing.Value('i', 0)

    if num_workers is None or num_workers > 1:
        pool = multiprocessing.Pool(processes=num_workers, initializer=init_worker,
                                    initargs=(method, counter, fail_counter, model, timeout, max_program_len,
                                              max_beam_size))
        return pool.starmap(solve_problem_worker, zip(problems, repeat(threshold)))
    else:
        # Don't run in pool to enable debugging
        init_worker(method, counter, fail_counter, model, timeout, max_program_len, max_beam_size)
        return [solve_problem_worker(data, threshold) for data in problems]


def solve_problem_worker(data, threshold):
    examples = Example.from_line(data)
    env = ProgramEnv(examples)
    userProgram = Program.parse(data['program'])
    

    if method == 'beam':
        solution = cab(env, max_program_len, model, params.cab_beam_size, params.cab_width,
                       params.cab_width_growth, timeout, max_beam_size=max_beam_size)
    elif method == 'dfs':
        solution = dfs(env, max_program_len, model, params.dfs_max_width, timeout)
    elif method == 'beam_repair':
        solution = cab_repair(env, max_program_len, model, params.cab_beam_size, params.cab_width,
                              params.cab_width_growth, timeout, userProgram, threshold,
                              max_beam_size=max_beam_size)
    elif method == 'dfs_repair':
        solution = dfs_repair(env, max_program_len, model, params.dfs_max_width, timeout,
                              userProgram, threshold)

    counter.value += 1
    print("\rSolving problems... %d (failed: %d)" % (counter.value, fail_counter.value), end="")

    if solution['result'] is False:
        solution['result'] = "Failed"
        fail_counter.value += 1
    else:
        values = [Value.construct(x) for x in data['examples'][0]['inputs']]
        value_types = [x.type for x in values]
        solution['result'] = Program(value_types, solution['result']).encode()
    solution['changedOp'] = data['changedOp']
    return solution


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input_path', type=str)
    parser.add_argument('output_path', type=str)
    parser.add_argument('model_path', type=str)
    parser.add_argument('timeout', type=int)
    parser.add_argument('max_program_len', type=int)
    parser.add_argument('--num_workers', type=int, default=None)
    parser.add_argument('--max_beam_size', type=int, default=819200)
    parser.add_argument('--search_method', choices=['beam', 'dfs', 'beam_repair', 'dfs_repair'], default='beam')

    args = parser.parse_args()

    problems = load_problems(args.input_path)

    model = PCCoder()
    model.load(args.model_path)

    model.eval()

    res = solve_problems(problems, args.search_method, model, args.timeout, args.max_program_len,
                         args.max_beam_size, args.num_workers, 1)
    print("")

    solved = len([x for x in res if x['result'] != 'Failed'])
    print("Solved: %d\\%d:" % (solved, len(res)), str(100.0 * solved / len(res)) + '%')

    open(args.output_path, 'w').write('\n'.join([json.dumps(x) for x in res]))


if __name__ == '__main__':
    main()
