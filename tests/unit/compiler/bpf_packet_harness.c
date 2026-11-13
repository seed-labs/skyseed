/* Offline helper substitutes around the actual packaged C program.
 * TC push/pop are simulated here, not claimed as a kernel integration test.
 */
#include <string.h>
#include "label_forwarding.bpf.c"

static unsigned char *frame;
static int frame_length;
static int bound;
static struct match_key expected;
static __u32 published;
static __u64 readers[3];
static __u32 versions[2][3];
static struct rule_value rules[2][3];
static int table_markers[2][3];
static int node;
static int publication_race;
static int selected_version;
static int protected_read;
static struct port_binding binding = { .ifindex = 77, .source_mac = {2,1,2,3,4,5},
                                      .peer_mac = {2,6,7,8,9,10} };

static void *lookup_mock(void *map, const void *key)
{
    if (map == &active_generation)
        return &published;
    if (map == &generation_readers) {
        __u32 generation = *(__u32 *)key;
        if (publication_race == 1) {
            published = 2;
            publication_race = 0;
        }
        return generation > 0 && generation < 3 ? &readers[generation] : 0;
    }
    if (map == &node_versions) {
        __u32 generation = *(__u32 *)key;
        protected_read = readers[generation] == 1;
        if (publication_race == 2) {
            published = 2;
            publication_race = 0;
        }
        return &versions[node][generation];
    }
    if (map == &rule_versions) {
        selected_version = *(__u32 *)key;
        return selected_version ? &table_markers[node][selected_version] : 0;
    }
    if (map == &port_bindings)
        return bound && *(__u32 *)key == 1 ? &binding : 0;
    const unsigned char *query = (const unsigned char *)key + 4;
    const unsigned char *prefix = (const unsigned char *)&expected + 4;
    for (unsigned int bit = 0; bit < expected.prefixlen; bit++) {
        unsigned char mask = 1 << (7 - bit % 8);
        if ((query[bit / 8] & mask) != (prefix[bit / 8] & mask))
            return 0;
    }
    return &rules[node][selected_version];
}

static long load_mock(struct __sk_buff *skb, __u32 offset, void *output, __u32 size)
{
    if (offset + size > (unsigned int)frame_length)
        return -1;
    memcpy(output, frame + offset, size);
    return 0;
}

static long store_mock(struct __sk_buff *skb, __u32 offset, const void *input, __u32 size, __u64 flags)
{
    if (offset + size > (unsigned int)frame_length)
        return -1;
    memcpy(frame + offset, input, size);
    return 0;
}

static long redirect_mock(__u32 ifindex, __u64 flags) { return ifindex == 77 ? TC_ACT_REDIRECT : TC_ACT_SHOT; }
static long type_mock(struct __sk_buff *skb, __u32 kind) { return kind == 0 ? 0 : -1; }
static long checksum_mock(struct __sk_buff *skb, __u32 offset, __u64 old, __u64 new, __u64 size) { return 0; }

static int execute_packet(unsigned char *packet, int *length)
{
    frame = packet;
    frame_length = *length;
    selected_version = 0;
    protected_read = 0;
    map_lookup = lookup_mock;
    load_bytes = load_mock;
    store_bytes = store_mock;
    redirect_packet = redirect_mock;
    change_type = type_mock;
    replace_checksum = checksum_mock;
    struct __sk_buff skb = {};
    int result = decide(&skb);
    if (publication_race == 3) {
        published = 2;
        publication_race = 0;
    }
    if (result == TC_ACT_UNSPEC && needs_push(&skb)) {
        memmove(frame + 18, frame + 14, frame_length - 14);
        frame_length += 4;
        frame[12] = 0x88;
        frame[13] = 0x47;
        result = complete_push(&skb);
    } else if (result == TC_ACT_UNSPEC && needs_pop(&skb)) {
        memmove(frame + 14, frame + 18, frame_length - 18);
        frame_length -= 4;
        frame[12] = 0x08;
        frame[13] = 0x00;
        result = complete_pop(&skb);
    }
    *length = frame_length;
    return result;
}

int run_packet(unsigned char *packet, int *length, const void *key_bytes, const void *value_bytes, int available)
{
    memcpy(&expected, key_bytes, sizeof(expected));
    memcpy(&rules[0][1], value_bytes, sizeof(struct rule_value));
    published = 1;
    node = 0;
    versions[0][1] = 1;
    memset(readers, 0, sizeof(readers));
    bound = available;
    publication_race = 0;
    return execute_packet(packet, length);
}

void prepare_generations(const void *key_bytes, const void *old_rule,
                         const void *new_rule, const void *unchanged_rule)
{
    memcpy(&expected, key_bytes, sizeof(expected));
    memcpy(&rules[0][1], old_rule, sizeof(struct rule_value));
    memcpy(&rules[0][2], new_rule, sizeof(struct rule_value));
    memcpy(&rules[1][1], unchanged_rule, sizeof(struct rule_value));
    /* Generation 2 changes node 0; node 1 still points to the same map. */
    versions[0][1] = 1;
    versions[0][2] = 2;
    versions[1][1] = 1;
    versions[1][2] = 1;
    published = 1;
    bound = 1;
    publication_race = 0;
    memset(readers, 0, sizeof(readers));
}

void publish_generation(int generation) { published = generation; }
void remove_node_version(int node_id, int generation) { versions[node_id][generation] = 0; }

int run_generation_packet(unsigned char *packet, int *length, int node_id, int race)
{
    node = node_id;
    publication_race = race;
    return execute_packet(packet, length);
}

int last_version(void) { return selected_version; }
int read_was_protected(void) { return protected_read; }
unsigned long long remaining_readers(void) { return readers[1] + readers[2]; }
