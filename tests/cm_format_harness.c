/* Native host check of the actual vendor WDA message builder, not a modem test. */
#include <assert.h>
#include "QMIThread.h"
#undef dbg_time
#define dbg_time(...) ((void)0)
#include "QMIThread.c"

int main(void) {
    unsigned char bytes[4096] = {0};
    PQMUX_MSG msg = (PQMUX_MSG)bytes;
    QMAP_SETTING settings = {0};
    QMODEM_WDA_CONTEXT context = { &settings, 1 };
    WdaSetDataFormat(msg, &context);
    PQMIWDS_ADMIN_SET_DATA_FORMAT_TLV_QOS qos = (void *)(&msg->QMUXMsgHdr + 1);
    PQMIWDS_ADMIN_SET_DATA_FORMAT_TLV link = (void *)(qos + 1);
    assert(le32_to_cpu(link->Value) == 1);
    context.link_protocol = 2;
    WdaSetDataFormat(msg, &context);
    assert(le32_to_cpu(link->Value) == 2);
    settings.rx_urb_size = 4096;
    context.link_protocol = 1;
    WdaSetDataFormat(msg, &context);
    assert(le32_to_cpu(msg->SetDataFormatReq.UnderlyingLinkLayerProtocolTlv.Value) == 2);
    puts("PASS: legacy Ethernet, managed Raw-IP and existing QMAP format encoding");
    return 0;
}
