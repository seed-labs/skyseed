/* SPDX-License-Identifier: GPL-2.0
 * One node-local table: exact MPLS labels first, IPv4 ingress prefixes second.
 * TC MPLS actions do push/pop; this program owns all forwarding decisions.
 */
#include <linux/bpf.h>
#include <linux/pkt_cls.h>
#include <linux/if_ether.h>
#include <linux/ip.h>

#define SEC(name) __attribute__((section(name), used))
#define UINT(name, value) int (*name)[value]
#define TYPE(name, value) value *name
#define INLINE static __attribute__((always_inline)) inline
#if __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
#define BE16(value) __builtin_bswap16((__u16)(value))
#define BE32(value) __builtin_bswap32((__u32)(value))
#else
#define BE16(value) ((__u16)(value))
#define BE32(value) ((__u32)(value))
#endif

enum { PUSH = 1, SWAP = 2, POP_DELIVER = 3, FORWARD = 4, DELIVER = 5 };
struct match_key { __u32 prefixlen; __be32 kind; __be32 value; };
struct rule_value { __u32 action; __u32 port; __u32 label; };
struct port_binding { __u32 ifindex; __u8 source_mac[6]; __u8 peer_mac[6]; };

struct rule_table {
    UINT(type, BPF_MAP_TYPE_LPM_TRIE);
    UINT(max_entries, 1048576);
    UINT(map_flags, BPF_F_NO_PREALLOC);
    TYPE(key, struct match_key);
    TYPE(value, struct rule_value);
};
struct rule_table rules_template SEC(".maps");
/* Shared by all nodes: one publication selects their prepared versions. */
struct {
    UINT(type, BPF_MAP_TYPE_ARRAY);
    UINT(max_entries, 1);
    TYPE(key, __u32);
    TYPE(value, __u32);
} active_generation SEC(".maps");
struct {
    UINT(type, BPF_MAP_TYPE_HASH);
    UINT(max_entries, 128);
    UINT(map_flags, BPF_F_NO_PREALLOC);
    TYPE(key, __u32);
    TYPE(value, __u64);
} generation_readers SEC(".maps");
/* Node-local: unchanged nodes can reuse one table across generations. */
struct {
    UINT(type, BPF_MAP_TYPE_HASH);
    UINT(max_entries, 128);
    UINT(map_flags, BPF_F_NO_PREALLOC);
    TYPE(key, __u32);
    TYPE(value, __u32);
} node_versions SEC(".maps");
struct {
    UINT(type, BPF_MAP_TYPE_HASH_OF_MAPS);
    UINT(max_entries, 128);
    UINT(map_flags, BPF_F_NO_PREALLOC);
    TYPE(key, __u32);
    struct rule_table *values[];
} rule_versions SEC(".maps") = { .values = { [0] = &rules_template } };
struct {
    UINT(type, BPF_MAP_TYPE_HASH);
    UINT(max_entries, 4096);
    UINT(map_flags, BPF_F_NO_PREALLOC);
    TYPE(key, __u32);
    TYPE(value, struct port_binding);
} port_bindings SEC(".maps");

static void *(*map_lookup)(void *, const void *) = (void *)BPF_FUNC_map_lookup_elem;
static long (*load_bytes)(struct __sk_buff *, __u32, void *, __u32) = (void *)BPF_FUNC_skb_load_bytes;
static long (*store_bytes)(struct __sk_buff *, __u32, const void *, __u32, __u64) = (void *)BPF_FUNC_skb_store_bytes;
static long (*redirect_packet)(__u32, __u64) = (void *)BPF_FUNC_redirect;
static long (*change_type)(struct __sk_buff *, __u32) = (void *)BPF_FUNC_skb_change_type;
static long (*replace_checksum)(struct __sk_buff *, __u32, __u64, __u64, __u64) = (void *)BPF_FUNC_l3_csum_replace;

INLINE int read_rule(const struct match_key *key, struct rule_value *output)
{
    __u32 zero = 0;
    volatile __u32 *publication = map_lookup(&active_generation, &zero);
    if (!publication)
        return 0;
    /* Pin and recheck before reading generation-owned mappings. Retired IDs
     * are never republished; Runner waits for readers before reclaiming them.
     */
    for (int attempt = 0; attempt < 3; attempt++) {
        __u32 generation = *publication;
        __u64 *readers = map_lookup(&generation_readers, &generation);
        if (!readers)
            continue;
        __sync_fetch_and_add(readers, 1);
        if (*publication != generation) {
            __sync_fetch_and_add(readers, -1);
            continue;
        }
        int found = 0;
        __u32 *version = map_lookup(&node_versions, &generation);
        if (version) {
            void *table = map_lookup(&rule_versions, version);
            if (table) {
                struct rule_value *rule = map_lookup(table, key);
                if (rule) {
                    *output = *rule;
                    found = 1;
                }
            }
        }
        __sync_fetch_and_add(readers, -1);
        return found;
    }
    return 0;
}

INLINE int send_to_port(struct __sk_buff *skb, __u32 port)
{
    struct port_binding *binding = map_lookup(&port_bindings, &port);
    if (!binding || !binding->ifindex)
        return TC_ACT_SHOT;
    struct port_binding output = *binding;
    if (store_bytes(skb, 0, output.peer_mac, 6, 0) < 0 ||
        store_bytes(skb, 6, output.source_mac, 6, 0) < 0)
        return TC_ACT_SHOT;
    return redirect_packet(output.ifindex, 0);
}

INLINE int deliver_local(struct __sk_buff *skb)
{
    /* PACKET_HOST: the local prefix/address is installed with this rule. */
    if (change_type(skb, 0) < 0)
        return TC_ACT_SHOT;
    return TC_ACT_OK;
}

SEC("classifier")
int decide(struct __sk_buff *skb)
{
    __be16 protocol;
    __be32 shim = 0;
    struct iphdr ip = {};
    struct match_key key = { .prefixlen = 64 };
    skb->cb[0] = 0;
    if (load_bytes(skb, 12, &protocol, sizeof(protocol)) < 0)
        return TC_ACT_SHOT;
    if (protocol == BE16(ETH_P_MPLS_UC)) {
        if (load_bytes(skb, ETH_HLEN, &shim, sizeof(shim)) < 0)
            return TC_ACT_SHOT;
        __u32 label_word = BE32(shim);
        if (!(label_word & 0x100) || (label_word >> 12) < 16 || !(label_word & 0xff))
            return TC_ACT_SHOT;
        key.value = BE32(label_word >> 12);
    } else if (protocol == BE16(ETH_P_IP)) {
        if (load_bytes(skb, ETH_HLEN, &ip, sizeof(ip)) < 0 || ip.version != 4 || ip.ihl < 5)
            return TC_ACT_SHOT;
        key.kind = BE32(1);
        key.value = ip.daddr;
    } else {
        return TC_ACT_OK;
    }

    /* Copy under the generation's reader reference. No later stage reads it. */
    struct rule_value rule = {};
    if (!read_rule(&key, &rule))
        return TC_ACT_SHOT;
    if (protocol == BE16(ETH_P_MPLS_UC)) {
        if (rule.action == POP_DELIVER) {
            __u8 version;
            if (load_bytes(skb, ETH_HLEN + 4, &version, 1) < 0 || version >> 4 != 4)
                return TC_ACT_SHOT;
            skb->cb[0] = POP_DELIVER;
            return TC_ACT_UNSPEC;
        }
        if (rule.action != SWAP || (BE32(shim) & 0xff) <= 1)
            return TC_ACT_SHOT;
        __u32 word = BE32(shim);
        shim = BE32((rule.label << 12) | (word & 0xf00) | ((word & 0xff) - 1));
        if (store_bytes(skb, ETH_HLEN, &shim, sizeof(shim), 0) < 0)
            return TC_ACT_SHOT;
        return send_to_port(skb, rule.port);
    }
    if (rule.action == PUSH) {
        skb->cb[0] = PUSH;
        skb->cb[1] = rule.port;
        skb->cb[2] = rule.label;
        return TC_ACT_UNSPEC;
    }
    if (rule.action == DELIVER)
        return deliver_local(skb);
    if (rule.action != FORWARD || ip.ttl <= 1)
        return TC_ACT_SHOT;
    __u16 old_ttl = BE16((__u16)ip.ttl << 8);
    ip.ttl--;
    __u16 new_ttl = BE16((__u16)ip.ttl << 8);
    if (replace_checksum(skb, ETH_HLEN + 10, old_ttl, new_ttl, 2) < 0 ||
        store_bytes(skb, ETH_HLEN + 8, &ip.ttl, 1, 0) < 0)
        return TC_ACT_SHOT;
    return send_to_port(skb, rule.port);
}

SEC("classifier")
int needs_push(struct __sk_buff *skb) { return skb->cb[0] == PUSH ? -1 : 0; }

SEC("classifier")
int needs_pop(struct __sk_buff *skb) { return skb->cb[0] == POP_DELIVER ? -1 : 0; }

SEC("action")
int complete_push(struct __sk_buff *skb)
{
    __be32 shim = BE32((skb->cb[2] << 12) | 0x100 | 64);
    if (store_bytes(skb, ETH_HLEN, &shim, sizeof(shim), 0) < 0)
        return TC_ACT_SHOT;
    return send_to_port(skb, skb->cb[1]);
}

SEC("action")
int complete_pop(struct __sk_buff *skb) { return deliver_local(skb); }

char license_text[] SEC("license") = "GPL";
