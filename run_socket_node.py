import gevent
from gevent import monkey;

import network

monkey.patch_all(thread=False)

import time
import random
import traceback
from typing import List, Callable
from gevent import Greenlet
from myexperiements.sockettest.sdumbo_node import SDumboBFTNode
from myexperiements.sockettest.sdumbo_dy_node import SDumboDYNode
from myexperiements.sockettest.dkr_node import ADKRNode
from myexperiements.sockettest.dkr_bn_node import ADKRBNNode
from network.socket_server import NetworkServer
from network.socket_client import NetworkClient
from network.socket_client_ng import NetworkClient
from multiprocessing import Value as mpValue, Queue as mpQueue
from ctypes import c_bool
from charm.toolbox.ecgroup import ECGroup,G
group = ECGroup(714)
def instantiate_bft_node(sid, i, B, N, N_g, l, recon, f, K, bft_from_server: Callable, bft_to_client: Callable, ready: mpValue,
                         stop: mpValue, protocol="sdumbo-dy", mute=False, F=100, debug=False, omitfast=False, countpoint=0, m=0):
    bft = None
    print("m=", m)
    # if protocol == 'dumbo':
    #     bft = DumboBFTNode(sid, i, B, N, f, bft_from_server, bft_to_client, ready, stop, K, mute=mute, debug=debug)
    if protocol == 'sdumbo':
        bft = SDumboBFTNode(sid, i, B, N, f, bft_from_server, bft_to_client, ready, stop, K, mute=mute, debug=debug)
    elif protocol == 'sdumbo-dy':
        bft = SDumboDYNode(sid, i, B, l, f, N_g, N, recon, bft_from_server, bft_to_client, ready, stop, K, m, mute=mute, debug=debug)
    elif protocol == 'adkr':
        bft = ADKRNode(sid, i, B, l, f, N_g, N, recon, bft_from_server, bft_to_client, ready, stop, K, mute=mute, debug=debug)
    elif protocol == 'adkr-bn':
        bft = ADKRBNNode(sid, i, B, l, f, N_g, N, recon, bft_from_server, bft_to_client, ready, stop, K, mute=mute, debug=debug)

    # elif protocol == 'ng':
    #     bft = NGSNode(sid, i, S, B, F, N, f, bft_from_server, bft_to_client, ready, stop, mute=mute, countpoint=countpoint)
    else:
        print("Only support sdumbo/dy")
    return bft


if __name__ == '__main__':

    import argparse
    print("main")
    parser = argparse.ArgumentParser()
    parser.add_argument('--sid', metavar='sid', required=True,
                        help='identifier of node', type=str)
    parser.add_argument('--id', metavar='id', required=True,
                        help='identifier of node', type=int)
    parser.add_argument('--N', metavar='N', required=True,
                        help='number of parties', type=int)
    parser.add_argument('--Ng', metavar='Ng', required=False,
                        help='number of parties', type=int)
    parser.add_argument('--f', metavar='f', required=True,
                        help='number of faulties', type=int)
    parser.add_argument('--B', metavar='B', required=True,
                        help='size of batch', type=int)
    parser.add_argument('--l', metavar='l', required=False,
                        help='number of leaving nodes', type=int, default=0.1)
    parser.add_argument('--recon', metavar='recon', required=False,
                        help='frequency for reconfiguration', type=int)
    parser.add_argument('--K', metavar='K', required=False,
                        help='instance to execute', type=int)
    parser.add_argument('--S', metavar='S', required=False,
                        help='slots to execute', type=int, default=50)
    parser.add_argument('--P', metavar='P', required=False,
                        help='protocol to execute', type=str, default="ng")
    parser.add_argument('--M', metavar='M', required=False,
                        help='whether to mute a third of nodes', type=bool, default=False)
    parser.add_argument('--F', metavar='F', required=False,
                        help='batch size of fallback path', type=int, default=100)
    parser.add_argument('--D', metavar='D', required=False,
                        help='whether to debug mode', type=bool, default=False)
    parser.add_argument('--O', metavar='O', required=False,
                        help='whether to omit the fast path', type=bool, default=False)
    parser.add_argument('--C', metavar='C', required=False,
                        help='point to start measure tps and latency', type=int, default=0)
    parser.add_argument('--m', metavar='m', required=False,
                       help='malicious behavior(m=0 for close and random m>0 for open)', type=int, default=0)
    args = parser.parse_args()

    # Some parameters
    sid = args.sid
    i = args.id
    N = args.N
    N_g = args.Ng
    f = args.f
    l = args.l
    B = args.B
    recon = args.recon
    K = args.K
    S = args.S
    P = args.P
    M = args.M
    F = args.F
    D = args.D
    O = args.O
    C = args.C
    m = args.m
    # r = args.r

    # Random generator
    rnd = random.Random(sid)

    # Nodes list
    addresses = [None] * N
    try:
        with open('hosts.config', 'r') as hosts:
            for line in hosts:
                params = line.split()
                pid = int(params[0])
                priv_ip = params[1]
                pub_ip = params[2]
                port = int(params[3])
                if pid not in range(N):
                    continue
                if pid == i:
                    # print(pid, priv_ip, port)
                    my_address = (priv_ip, port)
                addresses[pid] = (pub_ip, port)
        assert all([node is not None for node in addresses])
        # print("hosts.config is correctly read")


        client_bft_mpq = mpQueue()
        #client_from_bft = client_bft_mpq.get
        client_from_bft = lambda: client_bft_mpq.get(timeout=0.00001)

        bft_to_client = client_bft_mpq.put_nowait

        server_bft_mpq = mpQueue()
        #bft_from_server = server_bft_mpq.get
        bft_from_server = lambda: server_bft_mpq.get(timeout=0.00001)
        server_to_bft = server_bft_mpq.put_nowait

        client_ready = mpValue(c_bool, False)
        server_ready = mpValue(c_bool, False)
        net_ready = mpValue(c_bool, False)
        stop = mpValue(c_bool, False)

        net_client = network.socket_client.NetworkClient(my_address[1], my_address[0], i, addresses, client_from_bft, client_ready, stop)
        net_server = NetworkServer(my_address[1], my_address[0], i, addresses, server_to_bft, server_ready, stop)
        print("here debug = ", D)
        print("here N = ", N)
        bft = instantiate_bft_node(sid, i, B, N, N_g, l, recon, f, K, bft_from_server, bft_to_client, net_ready, stop, P, M, F, D, O, C, m)

        net_server.start()
        net_client.start()

        while not client_ready.value or not server_ready.value:
            time.sleep(1)
            print("waiting for network ready...")

        # gevent.sleep(3)
        time.sleep(3)

        with net_ready.get_lock():
            net_ready.value = True

        bft_thread = Greenlet(bft.run)
        bft_thread.start()
        bft_thread.join()

        with stop.get_lock():
            stop.value = True

        net_client.terminate()
        net_client.join()
        time.sleep(1)
        net_server.terminate()
        net_server.join()

    except FileNotFoundError or AssertionError as e:
        traceback.print_exc()
