#define main MailBridgeApplicationMain
#include "../src/local_mail_agent/native/mail_bridge.m"
#undef main
int main(int argc,const char *argv[]){@autoreleasepool{
    if(argc!=2)return 2;
    NSData *data=[NSData dataWithContentsOfFile:@(argv[1])];
    NSArray *cases=[NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    NSMutableArray *out=[NSMutableArray array];
    for(NSDictionary *test in cases)[out addObject:ReceiptCheck(test[@"source"],test[@"request"])];
    NSData *result=[NSJSONSerialization dataWithJSONObject:out options:0 error:nil];
    [NSFileHandle.fileHandleWithStandardOutput writeData:result];return 0;
}}
