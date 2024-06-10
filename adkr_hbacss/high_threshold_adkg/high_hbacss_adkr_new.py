import copy

from gevent import monkey; monkey.patch_all(thread=False)

import json
import logging
import os
import traceback, time
import gevent
import numpy as np
import hashlib
import pickle
from collections import namedtuple
from enum import Enum
from gevent import Greenlet
from gevent.queue import Queue
from collections import defaultdict
from gevent.event import Event
from honeybadgerbft.exceptions import UnknownTagError
from utils.core.betterpairing import G1, ZR
from utils.core.betterpairing import inner_product, mulexp
from utils.core.serializer import serialize, deserialize
from adkr_hbacss.high_threshold_adkg.tthresholdmvba_inter import tthresholdmvba
from adkr.adkr_high.adkg_adp.smvba_dy_inter import speedmvba
from utils.core.common_coin_ped import shared_coin
from utils.core.reliablebroadcast import reliablebroadcast
from utils.core.pillaragreement import twovaluepillaragreement

from utils.core.pok import pok_Verify, proof_of_knowledge
from utils.core.degreecheck import check_degree,gen_dual_code
from adkr.acss.core.polynomial_pairing import polynomials_over_BN
from adkr.keyrefersh.core.poly_misc_bn import interpolate_at_x1, interpolate_g1_at_x
from adkr_hbacss.high_threshold_adkg.hbacss_rbc_dual import completesecretsharing
from pickle import dumps, loads
import math
from utils.core.serializer import serialize_G2,deseralize_G2
def hash(x):
    return hashlib.sha256(pickle.dumps(x)).digest()


class BroadcastTag(Enum):
    ADKR_ACSS = 'ADKR_ACSS'
    ADKG_RBC = 'ADKG_RBC'
    ADKG_RANDEX = 'ADKG_RANDEX'
    ADKG_KEY = 'ADKG_KEY'
    ADKR_COMPLAIN = 'ADKR_COMPLAIN'
    ADKR_ABA = 'ADKR_ABA'
    ADKG_COIN = 'ADKG_COIN'
    ADKR_MVBA = 'ADKR_MVBA'


BroadcastReceiverQueues = namedtuple(
    'BroadcastReceiverQueues', (
        'ADKR_ACSS',
        'ADKG_RBC',
        'ADKG_RANDEX',
        'ADKG_KEY',
        'ADKR_COMPLAIN',
        'ADKR_ABA',
        'ADKR_MVBA',
        'ADKG_COIN'))


def broadcast_receiver_loop(recv_func, recv_queues, logger):
    while True:
        #gevent.sleep(0)
        sender, (tag, j, msg) = recv_func()

        # print("recv", (sender, tag))
        # logger.info('recv %s %s' % (sender, tag))
        if tag not in BroadcastTag.__members__:
            # TODO Post python 3 port: Add exception chaining.
            # See https://www.python.org/dev/peps/pep-3134/
            raise UnknownTagError('Unknown tag: {}! Must be one of {}.'.format(
                tag, BroadcastTag.__members__.keys()))
        recv_queue = recv_queues._asdict()[tag]


        try:
            if tag in {BroadcastTag.ADKR_ACSS.value,
                       BroadcastTag.ADKG_RBC.value,
                       BroadcastTag.ADKR_ABA.value,
                       BroadcastTag.ADKG_COIN.value}:
                # print("put to ", j, "queue")
                recv_queue = recv_queue[j]

            recv_queue.put_nowait((sender, msg))

        except AttributeError as e:
            print("error", sender, (tag, j, msg))
            traceback.print_exc(e)


def ADKR_old_c(sid, pid, C_o, C_n, f, K, g, h, PK1, PK1s, SK1, ePKs, eSK, send, send2, recv, logger=None):
    """AKDR object used to run the protocol.

    :param str sid: The base name of the common coin that will be used to
        derive a nonce to uniquely identify the coin.
    :param int pid: Node id.
    :param int B: Batch size of transactions.
    :param int N: Number of nodes in the network.
    :param int f: Number of faulty nodes that can be tolerated.
    :param TBLSPublicKey sPK: Public key of the (f, N) threshold signature
        (:math:`\mathsf{TSIG}`) scheme.
    :param TBLSPrivateKey sSK: Signing key of the (f, N) threshold signature
        (:math:`\mathsf{TSIG}`) scheme.
    :param TBLSPublicKey sPK1: Public key of the (N-f, N) threshold signature
        (:math:`\mathsf{TSIG}`) scheme.
    :param TBLSPrivateKey sSK1: Signing key of the (N-f, N) threshold signature
        (:math:`\mathsf{TSIG}`) scheme.
    :param list sPK2s: Public key(s) of ECDSA signature for all N parties.
    :param PrivateKey sSK2: Signing key of ECDSA signature.
    :param str ePK: Public key of the threshold encryption
        (:math:`\mathsf{TPKE}`) scheme.
    :param str eSK: Signing key of the threshold encryption
        (:math:`\mathsf{TPKE}`) scheme.
    :param send:
    :param recv:
    :param K: a test parameter to specify break out after K rounds
    """


    N_o = len(C_o)
    N_n = len(C_n)

    f_o = f

    f_n = f



    # Unique sid for each round
    # print("old", C_o)
    # print("new", C_n)
    my_acss_input = Queue(1)

    acss_value_outputs = [Queue(1) for _ in range(N_o)]
    acss_outputs = [Queue(1) for _ in range(N_o)]

    acss_output_index = Queue()
    acss_output_count = set()
    acss_commit_a = [[None for _ in range(f_n+1)] for _ in range(N_o)]
    acss_commit_b = [[None for _ in range(f_n+1)] for _ in range(N_o)]
    acss_commit_n = [[None for _ in range(N_n)] for _ in range(N_o)]
    acss_share = [None for _ in range(N_n)]



    t = [None for _ in range(N_o)]
    count_num = Queue()
    vaba_input = Queue(1)
    vaba_output = Queue(1)
    aba_outputs = [Queue(1) for _ in range(N_o)]
    ba_signal = Event()
    acss_index = list()
    # print(pid, r, 'tx_to_send:')
    acss_threads = [None] * N_o

    acss_recvs = [Queue() for _ in range(N_o)]
    rbc_recvs = [Queue() for _ in range(N_o)]

    coin_recvs = [Queue() for _ in range(N_o)]
    randex_recv = Queue()
    key_recv = Queue()
    mvba_recvs = Queue()
    complain_recvs = [Queue() for _ in range(K)]
    aba_recvs = [Queue() for _ in range(N_o)]

    output_key = defaultdict()
    secret_a = 1
    secret_b = 2
    recv_queues = BroadcastReceiverQueues(

        ADKR_ACSS=acss_recvs,
        ADKG_RBC=rbc_recvs,
        ADKG_RANDEX=randex_recv,
        ADKG_KEY = key_recv,
        ADKR_COMPLAIN = complain_recvs,
        ADKR_ABA = aba_recvs,
        ADKG_COIN = coin_recvs,
        ADKR_MVBA = mvba_recvs
    )

    # print("g1", print(group.serialize(g)))
    s_time = time.time()
    def _setup_acss(j):
        """Setup the ACSS.
        :param int j: Node index for ACSS dealer.
        """

        def acss_send(k, o):
            """Reliable send operation.
            :param k: Node to send.
            :param o: Value to send.
            """
            send(k, ('ADKR_ACSS', j, o))

        # Only leader gets input
        acss_input = my_acss_input.get if C_o[j] == pid else None
        if C_o[j] == pid:
            my_acss_input.put_nowait((secret_a, secret_b))
        acss_thread = gevent.spawn(completesecretsharing, sid+'ACSS'+str(C_o[j]), pid,
                                 N_o, f_o, C_o, N_n, f_n, C_n, g, h, C_o[j], ePKs, eSK,
                                 acss_input, acss_value_outputs[j].put_nowait, receive=acss_recvs[j].get, send=acss_send, logger=logger)
        # print(pid, "start", j)
        def wait_for_acss_output():
            '''
            try:
                gevent.sleep(0)
                m = acss_thread.get()
            except Exception as e:
                print(e)
                # continue
            '''

            try:
                gevent.sleep(0)
                m = acss_value_outputs[j].get()
            except Exception as e:
                # print(e)
                pass

            s1 = time.time()
            (si, ri), (sbi, rbi), commits, commitsb = m
            acss_commit_a[j] = commits
            acss_commit_b[j] = commitsb
            """
            for t in range(N_n):
                re = G1.identity()
                re2 = G1.identity()
                for k in range(f + 1):
                    re *= deserialize(commits[k]) ** ((t + 1) ** k)
                    re2 *= deserialize(commitsb[k]) ** ((t + 1) ** k)
                acss_commit_n[j][t] = (re, re2)
            """
            acss_share[j] = ((ZR(si), ZR(ri)), (ZR(sbi), ZR(rbi)))
            print(pid, "adds", j, "shares")
            logger.info('%d acss complete in %f' % (j, (time.time()-s_time)))
            logger.info('%s acss comp %f' % (sid, (time.time()-s1)))
            acss_outputs[j].put_nowait(m)
            acss_output_index.put_nowait(j)
            acss_output_count.add(j)
            # acss_proofs[sid+'PB'+str(r)+str(j)] = proof
            # acss_value_outputs[j][0] = comms
            # acss_value_outputs[j][1] = encryptions
            # acss_value_outputs[j][2] = proofs

            if len(acss_output_count) == 2 * f_o + 1:
                # t = acss_output_count
                print(pid, "acss output", acss_output_count)
                logger.info('n-t acss complete in %f' % ((time.time() - s_time)))
                # vaba_input.put_nowait(acss_output_count)
                ba_signal.set()


                    #return False

        gevent.spawn(wait_for_acss_output)

        return acss_thread

    bc_recv_loop_thread = Greenlet(broadcast_receiver_loop, recv, recv_queues, logger)
    bc_recv_loop_thread.start()
    start = time.time()
    # N instances of PB
    for j in range(N_o):
        # print(pid, "start to set up ACSS %d" % j)
        _setup_acss(j)


    def make_vaba_send():  # this make will automatically deep copy the enclosed send func
        def vaba_send(k, o):
            """MVBA send operation.
            :param k: Node to send.
            :param o: Value to send.
            """
            send(k, ('ADKR_MVBA', 0, o))

        return vaba_send

    def make_coin_broadcast():  # this make will automatically deep copy the enclosed send func
        def coin_broadcast(o):
            """MVBA send operation.
            :param k: Node to send.
            :param o: Value to send.
            """
            # for i in range(N_o):
            #     send2(i, ('COIN_REQ', o))
            send2(-1, ('COIN_REQ', o))

        return coin_broadcast
    def make_vaba_predicate():
        def vaba_predicate(m, local_set):
            # print("??????")
            # print(m)
            if len(m) == 2 * f_o + 1:
                if m.issubset(local_set):
                    # print("true")
                    return True
            else:
                # print("false")
                return False

        return vaba_predicate


    ba_signal.wait()
    ba_signal.clear()
    t = set(list(acss_output_count)[:2 * f_o + 1])
    # print(t)

    vaba_thread =  gevent.spawn(tthresholdmvba, sid + 'AKDR-MVBA', pid, N_o, f_o, C_o, g,
                                vaba_input.get, vaba_output.put_nowait, mvba_recvs.get, make_vaba_send(), make_coin_broadcast(),
                                acss_output_index.get, predicate=make_vaba_predicate(), logger=logger)
    # vaba_thread =  gevent.spawn(speedmvba, sid + 'AKDR-MVBA', pid, N_o, f_o, 0, C_o, PK1, PK1s, SK1, g,
    #                             vaba_input.get, vaba_output.put_nowait, mvba_recvs.get, make_vaba_send(),
    #                             acss_output_index.get_nowait, predicate=make_vaba_predicate(), logger=logger)
    # gevent.sleep(2)
    vaba_input.put_nowait(t)

    # speedmvba(sid + 'AKDR-MVBA', pid, N_o, f_o, 0, C_o, PK1, PK1s, SK1, g,
    # vaba_input.get, vaba_output.put_nowait, mvba_recvs.get, make_vaba_send(),
    # acss_output_index.get_nowait, predicate = make_vaba_predicate(), logger = logger)
    # vaba_thread = gevent.spawn(speedmvba, sid + 'AKDR-MVBA', pid, N_o, f_o, 0, C_o, PK1, PK1s, SK1, g,
    #                     vaba_input.get, vaba_output.put_nowait, mvba_recvs.get, make_vaba_send(),
    #                     predicate=make_vaba_predicate(), logger=logger)

    # vaba_thread.start()




    # print("start mvba")


    # t = acss_output_count


    select_index = vaba_output.get()
    print(pid, "to mvba:", time.time()-start)
    print("acss_index:", select_index)

    if logger!= None:
        logger.info('mvba finis in %f, index %s' % (time.time()-start, select_index))

    for i in range(N_n):
        if i not in select_index:
            acss_share[i] = ((ZR(0), ZR(0)), (ZR(0), ZR(0)))


    # random extraction

    hm_1 = np.array([[ZR(i + 1) ** j for j in range(N_n)] for i in range(f_n + 1)])
    hm_2 = np.array([[ZR(i + 1) ** j for j in range(N_n)] for i in range(f_n)])
    mat1, mat2 = hm_1.tolist(), hm_2.tolist()
    z_shares = [ZR(0)] * (2*f_n + 1)
    r_shares = [ZR(0)] * (2*f_n + 1)
    for i in range(f_n + 1):
        # print("??????????", acss_share)
        secrets = [acss_share[j][0][0] for j in range(N_n)]
        randomness = [acss_share[j][0][1] for j in range(N_n)]
        z_shares[i] = inner_product(mat1[i], secrets)
        r_shares[i] = inner_product(mat1[i], randomness)
    # print("")
    for i in range(f_n, 2*f_n):
        secrets = [acss_share[j][1][0] for j in range(N_n)]
        randomness = [acss_share[j][1][1] for j in range(N_n)]
        # print(i + 1, i - (t), mat2[i - (t)])
        z_shares[i + 1] = inner_product(mat2[i - f_n], secrets)
        r_shares[i + 1] = inner_product(mat2[i - f_n], randomness)
    # print("zshare", len(z_shares))
    if logger:
        logger.info('compute z, r shares in %f' %(time.time()-start))
    poly = polynomials_over_BN(ZR)
    phi = poly(z_shares)

    phi_r = poly(r_shares)
    # print(phi(1), phi_r(1))
    for i in range(N_o):
        send(i, ('ADKG_RANDEX', 0, (phi(i+1), phi_r(i+1))))

    def share_and_OEC():
        share_list = []
        random_list = []
        while True:
            gevent.sleep(0)
            try:
                sender, msg = randex_recv.get()
                if logger:
                    logger.info('recv RANDEX in %f' % (time.time() - start))
            except Exception as e:
                continue
            s, r = msg
            share_list.append([sender+1, s])
            random_list.append([sender+1, r])
            if len(share_list) == f_o +1:
                if logger:
                    logger.info('recv f+1 pk shares in %f' % (time.time() - start))
                zi = interpolate_at_x1(share_list[:f + 1], 0, ZR(0))
                zi_s = interpolate_at_x1(random_list[:f + 1], 0, ZR(0))
                output_key['sk'] = zi
                output_key['sk_s'] = zi_s
                # print("z value", zi, zi_s)
                proof = proof_of_knowledge(g, g ** zi, zi)
                proof_s = proof_of_knowledge(h, h ** zi_s, zi_s)
                send(-1, ('ADKG_KEY', 0, (serialize(g ** zi), serialize(h ** zi_s), proof, proof_s)))
                break

            gevent.sleep(0)

    gevent.spawn(share_and_OEC)
    pk_shares = []
    gz_shares =[None for _ in range(N_o)]
    def derivation():
        u = [None for i in range(N_o)]
        v = [None for i in range(N_o)]
        t0 = time.time()
        for i in range(N_n):
            if i not in select_index:
                u[i] = G1.identity()
                v[i] = G1.identity()
            else:
                # commit_inter = []
                # commitb_inter = []
                # for j in range(f_o+1):
                #     commit_inter.append([(j+1), acss_commit_n[i][j][0]])
                #     commitb_inter.append([(j + 1), acss_commit_n[i][j][1]])
                # u[i] = interpolate_g1_at_x(commit_inter, 0, G1.identity())
                # v[i] = interpolate_g1_at_x(commitb_inter, 0, G1.identity())
                # print(u[i]==deserialize(acss_commit_a[i][0]))
                u[i] = deserialize(acss_commit_a[i][0])

                v[i] = deserialize(acss_commit_b[i][0])
        c_coeffs = [G1.identity()] * (2*f_n + 1)
        c_shares = [ZR(0)] * (N_n)
        for i in range(f_n + 1):
            c_coeffs[i] = mulexp(u, mat1[i])
        for i in range(f_n, 2*f_n):
            c_coeffs[i + 1] = mulexp(v, mat2[i - f_n])
        # print("u", u)
        if logger:
            logger.info('comp c coeffs in %f' % (time.time()-t0))
        for i in range(N_n):
            re = G1.identity()
            for k in range(2*f_n + 1):
                re *= c_coeffs[k] ** ((i + 1) ** k)
            c_shares[i]=re
        if logger:
            logger.info('comp n c_shares in %f' % (time.time() - start))
        while True:
            gevent.sleep(0)
            try:
                sender, msg = key_recv.get()
                if logger:
                    logger.info('recv key in %f' % (time.time() - start))
            except Exception as e:
                continue
            sgz, shz_s, pi, pi_s = msg
            gz = deserialize(sgz)
            hz_s = deserialize(shz_s)
            if not pok_Verify(gz, g, pi):
                print("recv wrong gz from sender", sender)
                continue
            if not pok_Verify(hz_s, h, pi_s):
                print("recv wrong hz_s from sender", sender)
                continue
            if c_shares[sender] != gz*hz_s:
                print("wrong interpolate of", sender)
                continue
            gz_shares[sender] = gz

            pk_shares.append([sender+1, gz])
            if len(pk_shares) == N_n-f_n:
                poly = polynomials_over_BN(ZR)
                dual_codes = {}
                # dual_codes[(f_n, N_n)] = gen_dual_code(N_n, f_n, poly)
                # if check_degree(g, dual_codes, 2*f_n, gz_shares, poly):
                pk = interpolate_g1_at_x(pk_shares[:2*f_n+1], 0, G1.identity())
                output_key['pk'] = pk
                logger.info('output pk in %f'% (time.time()-start))
                for i in range(N_n):
                    if gz_shares[i] == None:
                        pk_shares.append([i+1, interpolate_g1_at_x(pk_shares[:2 * f_n + 1], i+1, G1.identity())])
                break

    t = gevent.spawn(derivation)
    t.join()

    end = time.time()
    print(end-start)
    if logger != None:
        logger.info('ADKR-OLD Delay at Node %d: ' % pid + str(end - start))
    # print(output_key['pk'])
    return output_key, pk_shares