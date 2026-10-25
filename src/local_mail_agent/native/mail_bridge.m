// Native Apple Mail adapter. Only the guarded receipt path sets read status.
#import <Cocoa/Cocoa.h>
#import <ScriptingBridge/ScriptingBridge.h>
#import <ApplicationServices/ApplicationServices.h>
#import <CommonCrypto/CommonDigest.h>
#include <sys/stat.h>

@interface Failures : NSObject <SBApplicationDelegate>
@property NSInteger code;
@end
@implementation Failures
- (id)eventDidFail:(const AppleEvent *)event withError:(NSError *)error {
    self.code = error.code; return nil;
}
@end

static NSString *Digest(NSString *s) {
    NSData *d=[s dataUsingEncoding:NSUTF8StringEncoding]; unsigned char hash[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(d.bytes,(CC_LONG)d.length,hash); NSMutableString *out=[NSMutableString string];
    for(int i=0;i<12;i++)[out appendFormat:@"%02x",hash[i]];return out;
}
static id Read(SBObject *obj, AEKeyword key) {return [[obj propertyWithCode:key] get];}
static void Output(NSDictionary *value) {
    NSData *d=[NSJSONSerialization dataWithJSONObject:value options:0 error:nil];
    if(d)[NSFileHandle.fileHandleWithStandardOutput writeData:d];
}
static OSStatus Permission(BOOL ask) {
    NSAppleEventDescriptor *target=[NSAppleEventDescriptor descriptorWithBundleIdentifier:@"com.apple.mail"];
    return AEDeterminePermissionToAutomateTarget(target.aeDesc,'core','getd',ask);
}
static NSString *Root(void) {
    return [[[NSBundle mainBundle] bundleURL] URLByDeletingLastPathComponent].path;
}
static void SaveStatus(NSString *state, NSInteger code) {
    NSString *path=[Root() stringByAppendingPathComponent:@"automation-status.json"];
    NSData *d=[NSJSONSerialization dataWithJSONObject:@{@"state":state,@"permission_code":@(code)} options:0 error:nil];
    [d writeToFile:path options:NSDataWritingAtomic error:nil];chmod(path.fileSystemRepresentation,0600);
}
static void FindInbox(SBObject *parent, NSMutableArray *out, Failures *f, int depth) {
    if(depth>6 || f.code)return;
    SBElementArray *boxes=[parent elementArrayWithCode:'mbxp'];
    for(SBObject *box in boxes){
        NSString *name=Read(box,'pnam');
        if(f.code)return;
        if([name isKindOfClass:NSString.class] &&
           ([@[@"inbox",@"收件箱",@"收件匣",@"收信箱"] containsObject:name.lowercaseString])){
            [out addObject:box];
        } else FindInbox(box,out,f,depth+1);
    }
}
static NSString *ReceiptHash(NSString *s) {
    NSData *d=[s dataUsingEncoding:NSUTF8StringEncoding];unsigned char hash[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(d.bytes,(CC_LONG)d.length,hash);NSMutableString *out=[NSMutableString string];
    for(int i=0;i<CC_SHA256_DIGEST_LENGTH;i++)[out appendFormat:@"%02x",hash[i]];return out;
}
static NSDictionary *ReceiptCheck(NSString *source, NSDictionary *request) {
    NSDictionary *guard=request[@"content_guard"];
    if(![guard isKindOfClass:NSDictionary.class]||[guard[@"version"] integerValue]!=2||
       ![guard[@"headers"] isKindOfClass:NSDictionary.class]||![guard[@"body_hash"] isKindOfClass:NSString.class]||
       [guard[@"body_hash"] length]!=64)return @{@"state":@"guard_missing"};
    if([Digest(source) isEqual:request[@"source_hash"]])return @{@"state":@"ok",@"validation":@"exact_source"};
    NSString *norm=[[source stringByReplacingOccurrencesOfString:@"\r\n" withString:@"\n"] stringByReplacingOccurrencesOfString:@"\r" withString:@"\n"];
    NSRange split=[norm rangeOfString:@"\n\n"];
    if(split.location==NSNotFound)return @{@"state":@"source_malformed"};
    NSString *head=[norm substringToIndex:split.location],*body=[norm substringFromIndex:split.location+2];
    if(![ReceiptHash(body) isEqual:guard[@"body_hash"]])return @{@"state":@"body_mismatch"};
    NSMutableDictionary *values=[NSMutableDictionary dictionary];NSString *last=nil;
    NSCharacterSet *space=[NSCharacterSet characterSetWithCharactersInString:@" \t\r\n"];
    NSRegularExpression *keyPattern=[NSRegularExpression regularExpressionWithPattern:@"^[A-Za-z0-9-]+$" options:0 error:nil];
    for(NSString *line in [head componentsSeparatedByString:@"\n"]){
        if(([line hasPrefix:@" "]||[line hasPrefix:@"\t"])&&last){NSMutableArray *a=values[last];a[a.count-1]=[a.lastObject stringByAppendingFormat:@" %@",[line stringByTrimmingCharactersInSet:space]];continue;}
        NSRange colon=[line rangeOfString:@":"];
        if(colon.location==NSNotFound)return @{@"state":@"source_malformed"};
        NSString *key=[line substringToIndex:colon.location];
        if([keyPattern numberOfMatchesInString:key options:0 range:NSMakeRange(0,key.length)]!=1)return @{@"state":@"source_malformed"};
        last=key.lowercaseString;if(!values[last])values[last]=[NSMutableArray array];
        [values[last] addObject:[[line substringFromIndex:colon.location+1] stringByTrimmingCharactersInSet:space]];
    }
    NSArray *keys=@[@"message-id",@"from",@"to",@"cc",@"bcc",@"subject",@"date",@"reply-to",@"mime-version",@"content-type",@"content-transfer-encoding",@"content-disposition"];
    NSDictionary *expected=guard[@"headers"];
    for(NSString *key in keys){
        NSString *current=values[key]?ReceiptHash([values[key] componentsJoinedByString:@"\n"]):nil;
        id wanted=expected[key];
        if((current||wanted)&&![current isEqual:wanted])return @{@"state":[key isEqual:@"message-id"]?@"identity_changed":@"identity_fields_changed"};
    }
    return @{@"state":@"ok",@"validation":@"normalized_content"};
}
static int MarkRead(NSString *requestFile, BOOL verifyOnly) {
    NSData *data=[NSData dataWithContentsOfFile:requestFile];
    id requests=data?[NSJSONSerialization JSONObjectWithData:data options:0 error:nil]:nil;
    if(![requests isKindOfClass:NSArray.class]||[requests count]>200){Output(@{@"ok":@NO,@"error":@"invalid_receipts"});return 2;}
    if([requests count]==0){Output(@{@"ok":@YES,@"results":@[]});return 0;}
    OSStatus permission=Permission(NO);
    if(permission!=noErr){Output(@{@"ok":@NO,@"error":@"automation_permission_required",@"code":@(permission)});return 2;}
    SBApplication *mail=[SBApplication applicationWithBundleIdentifier:@"com.apple.mail"];
    mail.timeout=60*30;Failures *f=[Failures new];mail.delegate=f;
    NSMutableArray *results=[NSMutableArray array];NSMutableDictionary *boxesByAccount=[NSMutableDictionary dictionary];
    @try {
        for(SBObject *account in [mail elementArrayWithCode:'mact']){
            f.code=0;if(![Read(account,'isac') boolValue]||f.code)continue;
            NSString *aid=Read(account,'ID  ');if(f.code||![aid isKindOfClass:NSString.class])continue;
            NSMutableArray *boxes=[NSMutableArray array];FindInbox(account,boxes,f,0);
            if(!f.code)boxesByAccount[Digest(aid)]=boxes;
        }
        for(id request in requests){
            if(![request isKindOfClass:NSDictionary.class])continue;
            NSString *identity=request[@"identity"];
            if(![identity isKindOfClass:NSString.class])continue;
            NSArray *pieces=[identity componentsSeparatedByString:@":"];
            NSString *state=boxesByAccount[pieces.firstObject]?@"not_found":@"account_unavailable";
            NSMutableDictionary *details=[NSMutableDictionary dictionary];
            if(pieces.count==2 && [pieces[1] length]>0 &&
               [pieces[1] rangeOfCharacterFromSet:NSCharacterSet.decimalDigitCharacterSet.invertedSet].location==NSNotFound){
                NSNumber *mid=@([pieces[1] longLongValue]);
                for(SBObject *box in boxesByAccount[pieces[0]]){
                    f.code=0;SBObject *message=[[box elementArrayWithCode:'mssg'] objectWithID:mid];
                    NSNumber *before=Read(message,'isrd'),*flag=Read(message,'isfl');
                    NSString *source=Read(message,'raso');
                    if(f.code||![before isKindOfClass:NSNumber.class]||![flag isKindOfClass:NSNumber.class]||![source isKindOfClass:NSString.class]){if(f.code)details[@"error_code"]=@(f.code);continue;}
                    [details removeObjectForKey:@"error_code"];
                    NSNumber *beforeWrite=Read(message,'isrd'),*flagBeforeWrite=Read(message,'isfl');
                    details[@"current_read"]=beforeWrite?:@NO;
                    if(f.code)details[@"error_code"]=@(f.code);
                    if(f.code||![before isEqual:beforeWrite]||![flag isEqual:flagBeforeWrite]){state=@"mail_state_changed";break;}
                    NSDictionary *check=ReceiptCheck(source,request);
                    if(![check[@"state"] isEqual:@"ok"]){state=check[@"state"];break;}
                    details[@"validation"]=check[@"validation"];
                    if(!verifyOnly&&!before.boolValue)[[message propertyWithCode:'isrd'] setTo:@YES];
                    NSNumber *after=Read(message,'isrd'),*flagAfter=Read(message,'isfl');
                    details[@"current_read"]=after?:@NO;
                    if(f.code)details[@"error_code"]=@(f.code);
                    if(verifyOnly&&!f.code&&[flag isEqual:flagAfter])state=after.boolValue?@"verified_read":@"still_unread";
                    else if(!f.code && after.boolValue && [flag isEqual:flagAfter])state=before.boolValue?@"already_read":@"marked_read";
                    else state=@"verification_failed";
                    break;
                }
            }
            details[@"identity"]=identity;details[@"state"]=state;[results addObject:details];
        }
        Output(@{@"ok":@YES,@"results":results});return 0;
    } @catch(NSException *exception){Output(@{@"ok":@NO,@"error":@"receipt_interface_failed",@"results":results});return 5;}
}
static int AccountAddresses(void) {
    OSStatus permission=Permission(NO);
    if(permission!=noErr){Output(@{@"ok":@NO,@"error":@"automation_permission_required"});return 2;}
    SBApplication *mail=[SBApplication applicationWithBundleIdentifier:@"com.apple.mail"];
    mail.timeout=60*30;Failures *f=[Failures new];mail.delegate=f;
    NSMutableArray *accounts=[NSMutableArray array];
    @try {
        for(SBObject *account in [mail elementArrayWithCode:'mact']){
            f.code=0;if(![Read(account,'isac') boolValue]||f.code)continue;
            NSString *aid=Read(account,'ID  ');id addresses=Read(account,'emad');
            if(!f.code && [aid isKindOfClass:NSString.class] && [addresses isKindOfClass:NSArray.class])
                [accounts addObject:@{@"key":Digest(aid),@"addresses":addresses}];
        }
        Output(@{@"ok":@YES,@"accounts":accounts});return 0;
    } @catch(NSException *exception){Output(@{@"ok":@NO,@"error":@"account_metadata_failed"});return 5;}
}
static int Collect(BOOL probe, NSString *knownFile, NSInteger sinceEpoch) {
    OSStatus permission=Permission(NO);
    if(permission!=noErr){Output(@{@"ok":@NO,@"error":@"automation_permission_required",@"code":@(permission)});return 2;}
    SBApplication *mail=[SBApplication applicationWithBundleIdentifier:@"com.apple.mail"];
    mail.timeout=60*30; // 30 seconds, Apple-event timeout units are ticks.
    Failures *f=[Failures new];mail.delegate=f;
    NSSet *known=[NSSet set];
    if(knownFile){NSData *d=[NSData dataWithContentsOfFile:knownFile];id v=d?[NSJSONSerialization JSONObjectWithData:d options:0 error:nil]:nil;
        if([v isKindOfClass:NSArray.class])known=[NSSet setWithArray:v];}
    NSMutableArray *accounts=[NSMutableArray array],*messages=[NSMutableArray array];
    NSInteger enabled=0,disabled=0;BOOL globalOK=YES;
    NSDate *since=[NSDate dateWithTimeIntervalSince1970:sinceEpoch],*until=[NSDate date];
    NSISO8601DateFormatter *iso=[NSISO8601DateFormatter new];
    @try {
        SBElementArray *all=[mail elementArrayWithCode:'mact'];
        for(SBObject *account in all){
            f.code=0;
            NSNumber *active=Read(account,'isac');
            if(f.code){globalOK=NO;[accounts addObject:@{@"state":@"account_read_failed",@"error_code":@(f.code)}];continue;}
            if(!active.boolValue){disabled++;continue;}
            enabled++;
            NSString *aid=Read(account,'ID  ');
            if(f.code||![aid isKindOfClass:NSString.class]){globalOK=NO;[accounts addObject:@{@"state":@"account_identifier_failed"}];continue;}
            NSString *key=Digest(aid);
            if(probe){[accounts addObject:@{@"key":key,@"state":@"enabled"}];continue;}
            NSMutableArray *inboxes=[NSMutableArray array];FindInbox(account,inboxes,f,0);
            if(f.code||inboxes.count==0){globalOK=NO;[accounts addObject:@{@"key":key,@"state":@"inbox_unavailable",@"error_code":@(f.code)}];continue;}
            NSInteger added=0,skipped=0,failed=0;BOOL truncated=NO;
            for(SBObject *box in inboxes){
                SBElementArray *allMessages=[box elementArrayWithCode:'mssg'];
                // Two bulk getter events avoid thousands of individual Apple events during initial sync.
                NSArray *identifiers=[allMessages arrayByApplyingSelector:NSSelectorFromString(@"id")];
                NSArray *dates=[allMessages arrayByApplyingSelector:NSSelectorFromString(@"dateReceived")];
                if(f.code){failed++;break;}
                if(identifiers.count!=dates.count){failed++;break;}
                // Fetch only metadata for ordering. Oldest pending messages get a turn first.
                NSMutableArray *refs=[NSMutableArray array];
                for(NSUInteger index=0;index<identifiers.count;index++){
                    NSNumber *mid=identifiers[index];NSDate *received=dates[index];
                    if(![mid isKindOfClass:NSNumber.class]||![received isKindOfClass:NSDate.class]){failed++;continue;}
                    if([received compare:since]==NSOrderedAscending||[received compare:until]==NSOrderedDescending)continue;
                    NSString *identity=[NSString stringWithFormat:@"%@:%@",key,mid];
                    if([known containsObject:identity]){skipped++;continue;}
                    SBObject *message=[allMessages objectWithID:mid];
                    [refs addObject:@{@"object":message,@"identity":identity,@"received":received}];
                }
                [refs sortUsingComparator:^NSComparisonResult(NSDictionary *a,NSDictionary *b){return [a[@"received"] compare:b[@"received"]];}];
                for(NSDictionary *ref in refs){
                    if(added>=50){truncated=YES;break;}
                    SBObject *message=ref[@"object"];
                    NSNumber *before=Read(message,'isrd');NSNumber *flagBefore=Read(message,'isfl');
                    NSNumber *size=Read(message,'msze');
                    if(f.code||![before isKindOfClass:NSNumber.class]||![flagBefore isKindOfClass:NSNumber.class]){failed++;f.code=0;continue;}
                    if(size.longLongValue>10*1024*1024){failed++;continue;}
                    NSString *source=Read(message,'raso');
                    NSNumber *after=Read(message,'isrd');NSNumber *flagAfter=Read(message,'isfl');
                    if(f.code||![source isKindOfClass:NSString.class]||source.length==0){failed++;f.code=0;continue;}
                    if(![before isEqual:after]||![flagBefore isEqual:flagAfter]){
                        Output(@{@"ok":@NO,@"error":@"mail_state_changed_abort",@"enabled_accounts":@(enabled)});return 3;
                    }
                    NSData *raw=[source dataUsingEncoding:NSUTF8StringEncoding];
                    if(raw.length>10*1024*1024){failed++;continue;}
                    [messages addObject:@{@"identity":ref[@"identity"],@"account":key,
                        @"received":[iso stringFromDate:ref[@"received"]],@"raw":source,
                        @"read_unchanged":@YES,@"flag_unchanged":@YES}];added++;
                }
            }
            if(failed)globalOK=NO;
            [accounts addObject:@{@"key":key,@"state":failed?@"partial":@"ok",@"queued":@(added),@"known":@(skipped),@"errors":@(failed),@"backlog":@(truncated)}];
        }
        if(f.code)globalOK=NO;
        Output(@{@"ok":@(globalOK),@"enabled_accounts":@(enabled),@"disabled_accounts":@(disabled),@"accounts":accounts,@"messages":messages,@"error_code":@(f.code)});
        return globalOK?0:4;
    } @catch(NSException *exception){Output(@{@"ok":@NO,@"error":@"mail_interface_failed"});return 5;}
}
int main(int argc,const char *argv[]){@autoreleasepool{
    umask(0077);
    NSString *mode=argc>1?@(argv[1]):@"--agent";
    if([mode isEqualToString:@"--account-addresses"])return AccountAddresses();
    if([mode isEqualToString:@"--mark-read"]||[mode isEqualToString:@"--verify-read"]){if(argc<3)return 2;return MarkRead(@(argv[2]),[mode isEqualToString:@"--verify-read"]);}
    if([mode isEqualToString:@"--probe"]||[mode isEqualToString:@"--collect"]){
        NSString *known=argc>2?@(argv[2]):nil;NSInteger since=argc>3?atoll(argv[3]):time(NULL)-86400;
        return Collect([mode isEqualToString:@"--probe"],known,since);
    }
    // Launched as an app so macOS displays the collector's own identity for consent.
    [NSApplication sharedApplication];[NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
    NSWindow *window=[[NSWindow alloc] initWithContentRect:NSMakeRect(0,0,440,160)
      styleMask:(NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskMiniaturizable)
      backing:NSBackingStoreBuffered defer:NO];
    window.title=@"Local Mail Agent";[window center];
    NSTextField *label=[NSTextField wrappingLabelWithString:@"Apple Mail 本机采集与分类\n时间、模型、已读策略及报告出口由配置文件决定。\n默认不修改原邮件，不向外发送内容。\n关闭此窗口不影响后台任务。"];
    label.frame=NSMakeRect(22,20,396,112);[window.contentView addSubview:label];[window makeKeyAndOrderFront:nil];
    SaveStatus(@"requesting_permission",0);
    OSStatus permission=Permission(YES);
    if(permission!=noErr){SaveStatus(@"awaiting_permission",permission);return 2;}
    SaveStatus(@"authorized",0);
    NSDictionary *info=NSBundle.mainBundle.infoDictionary;
    NSString *python=info[@"LMAPythonExecutable"],*configuration=info[@"LMAConfigPath"];
    if(![python isKindOfClass:NSString.class]||![configuration isKindOfClass:NSString.class]||
       ![[NSFileManager defaultManager] isExecutableFileAtPath:python]){
        SaveStatus(@"launcher_configuration_missing",1);return 1;
    }
    NSTask *task=[NSTask new];task.executableURL=[NSURL fileURLWithPath:python];
    task.arguments=@[@"-m",@"local_mail_agent",@"--config",configuration,@"run"];
    task.currentDirectoryURL=[NSURL fileURLWithPath:Root()];
    task.standardOutput=NSFileHandle.fileHandleWithNullDevice;task.standardError=NSFileHandle.fileHandleWithNullDevice;
    NSError *error=nil;if(![task launchAndReturnError:&error]){SaveStatus(@"worker_start_failed",error.code);return 1;}
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY,0),^{
        [task waitUntilExit];SaveStatus(@"worker_stopped",task.terminationStatus);
        dispatch_async(dispatch_get_main_queue(),^{[NSApp terminate:nil];});
    });
    [NSApp run];return task.terminationStatus;
}}
